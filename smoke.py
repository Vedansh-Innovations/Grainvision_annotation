"""End-to-end smoke test of the reordered GrainVision flow (Django test client)."""
import io, os, json, django
import numpy as np, cv2
from PIL import Image

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "grainvision.settings")
os.environ.setdefault("DEBUG", "True")
django.setup()

from django.test import Client
from django.core.files.uploadedfile import SimpleUploadedFile
from annotation.models import Submission, SubmissionStatus
from core.models import Commodity, Mandi, AuditLog

def plate():
    w=h=1000; img=np.full((h,w,3),20,np.uint8); cx,cy,r=w//2,h//2,int(w*0.42)
    cv2.circle(img,(cx,cy),r,(245,245,245),-1); rng=np.random.default_rng(7)
    for _ in range(30):
        a=rng.uniform(0,2*np.pi); rad=rng.uniform(0,r*0.78)
        px,py=int(cx+rad*np.cos(a)),int(cy+rad*np.sin(a))
        cv2.ellipse(img,(px,py),(int(rng.integers(15,24)),int(rng.integers(10,15))),float(rng.uniform(0,180)),0,360,(60,90,140),-1)
    b=io.BytesIO(); Image.fromarray(cv2.cvtColor(img,cv2.COLOR_BGR2RGB)).save(b,"JPEG",quality=95); return b.getvalue()

def step(m, ok):
    print(("  PASS  " if ok else "  FAIL  ")+m)
    if not ok: raise SystemExit("FAILED: "+m)

print("== ASSAYER FLOW (capture -> measure -> annotate) ==")
c = Client(); step("login ravi", c.login(username="ravi", password="assay12345"))
wheat = Commodity.objects.get(code="WHEAT")
mandi = Mandi.objects.filter(users__username="ravi").first()

# 1. new sample -> creates DRAFT, redirects to capture
r = c.post("/annotate/new/", {"commodity": wheat.id, "mandi": mandi.id})
step(f"new sample -> 302 ({r.status_code})", r.status_code==302 and "/capture/" in r.url)
sub_id = r.url.split("/")[2]
sub = Submission.objects.get(id=sub_id)
step("draft created, status=draft", sub.status==SubmissionStatus.DRAFT and sub.is_draft)
step("no measurements yet", not sub.measurements_done)

# 2. capture (STEP 1) -> saves image+particles, redirects to measurements
r = c.post(f"/annotate/{sub_id}/capture/submit/", {
    "image": SimpleUploadedFile("p.jpg", plate(), content_type="image/jpeg"),
    "quality_scores": json.dumps({"capture_mode":"auto","glare":False})})
step(f"capture -> 200 ({r.status_code})", r.status_code==200)
step("redirect target is measurements", "/measurements/" in r.json().get("redirect",""))
sub.refresh_from_db()
step(f"image saved + particles (n={sub.particle_count})", bool(sub.crop_image) and sub.particle_count>0)

# manual capture rejected
sub2 = Submission.objects.create(assayer=sub.assayer, commodity=wheat, mandi=mandi, sample_number=900, status=SubmissionStatus.DRAFT)
rm = c.post(f"/annotate/{sub2.id}/capture/submit/", {"image": SimpleUploadedFile("p.jpg", plate(), content_type="image/jpeg"), "quality_scores": json.dumps({"capture_mode":"manual"})})
step(f"manual capture rejected ({rm.status_code})", rm.status_code==400); sub2.delete()

# 3. measurements (STEP 2) - save & annotate later
r = c.get(f"/annotate/{sub_id}/measurements/"); step(f"measurements page ({r.status_code})", r.status_code==200)
r = c.post(f"/annotate/{sub_id}/measurements/", {
    "total_weight_g":"250.00","weight_good":"200.00","weight_broken":"10.90",
    "weight_foreign":"12.50","weight_fungal":"8.20","weight_immature":"18.40",
    "action":"later"})
step(f"save & later -> 302 workspace ({r.status_code})", r.status_code==302 and r.url.endswith("/annotate/"))
sub.refresh_from_db(); step("measurements saved", sub.measurements_done and float(sub.total_weight_g)==250.0)

# workspace shows it as a draft to continue
r = c.get("/annotate/"); step("workspace lists draft", r.status_code==200 and sub.short_id.encode() in r.content)

# resume -> should land on canvas (measurements done, unlabeled present)
r = c.get(f"/annotate/{sub_id}/resume/"); step(f"resume -> canvas ({r.status_code})", r.status_code==302 and "/canvas/" in r.url)

# 4. annotate all
r = c.get(f"/annotate/{sub_id}/canvas/"); step(f"canvas renders ({r.status_code})", r.status_code==200)
for p in sub.particles.all():
    c.post(f"/annotate/{sub_id}/label/", data=json.dumps({"particle_pk":p.id,"label":"good"}), content_type="application/json")
sub.refresh_from_db(); step("all labeled", sub.unlabeled_count==0)

# 5. review + submit
r = c.get(f"/annotate/{sub_id}/review/"); step(f"review ({r.status_code})", r.status_code==200)
r = c.post(f"/annotate/{sub_id}/submit/"); step(f"submit -> 302 ({r.status_code})", r.status_code==302)
sub.refresh_from_db(); step("status pending_qc + submitted", sub.status==SubmissionStatus.PENDING_QC and sub.submitted_at)

print("== QC FLOW (approve disappears, rework returns) ==")
q = Client(); step("login qc", q.login(username="qc", password="qc12345678"))
r = q.get("/qc/"); step("queue shows pending sample", r.status_code==200 and sub.public_id.encode() in r.content)

# rework path: send back to assayer
r = q.post(f"/qc/{sub_id}/rework/", {"notes":"Re-check the broken grains near the rim."})
step(f"request rework -> 302 ({r.status_code})", r.status_code==302)
sub.refresh_from_db()
step("status rework + submitted_at cleared", sub.status==SubmissionStatus.REWORK_REQUESTED and sub.submitted_at is None)
r = q.get("/qc/"); step("rework NOT in QC queue", sub.public_id.encode() not in r.content)

# assayer sees rework, re-annotates, resubmits
r = c.get("/annotate/"); step("rework appears in assayer workspace", sub.short_id.encode() in r.content)
r = c.get(f"/annotate/{sub_id}/resume/"); step("rework resume -> canvas", "/canvas/" in r.url)
r = c.post(f"/annotate/{sub_id}/submit/"); step(f"resubmit -> 302 ({r.status_code})", r.status_code==302)
sub.refresh_from_db(); step("back to pending_qc", sub.status==SubmissionStatus.PENDING_QC)
r = q.get("/qc/"); step("reappears in QC queue", sub.public_id.encode() in r.content)

# approve -> disappears
r = q.post(f"/qc/{sub_id}/approve/", {"notes":"Good."}); step(f"approve -> 302 ({r.status_code})", r.status_code==302)
sub.refresh_from_db(); step("status qc_approved", sub.status==SubmissionStatus.QC_APPROVED)
r = q.get("/qc/"); step("approved disappears from queue", sub.public_id.encode() not in r.content)

print("== ADMIN: mandi/commodity management + RBAC ==")
a = Client(); step("login admin", a.login(username="admin", password="admin12345"))
for path in ["/admin/","/admin/users/","/admin/reference/","/admin/dataset/","/admin/audit/"]:
    r=a.get(path); step(f"{path} ({r.status_code})", r.status_code==200)
# add a mandi
before = Mandi.objects.count()
r = a.post("/admin/reference/mandi/create/", {"name":"Test Mandi","district":"Testpur","state":"Testland"})
step(f"create mandi -> 302 ({r.status_code})", r.status_code==302 and Mandi.objects.count()==before+1)
# add a commodity
r = a.post("/admin/reference/commodity/create/", {"code":"SOY","name":"Soybean"})
step("create commodity", Commodity.objects.filter(code="SOY").exists())

print("== RBAC: assayer blocked from admin + qc ==")
step("assayer -> /admin/ blocked", c.get("/admin/").status_code in (302,403))
step("assayer -> /qc/ blocked", c.get("/qc/").status_code in (302,403))
step("assayer -> reference blocked", c.get("/admin/reference/").status_code in (302,403))

print("\nALL SMOKE TESTS PASSED")

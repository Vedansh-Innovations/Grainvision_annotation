"""
End-to-end tests for dynamic per-commodity annotation classes.

Run:  python manage.py test annotation.test_dynamic_classes -v 2
"""
import json

from django.test import TestCase
from django.urls import reverse

from accounts.models import User, Role
from core.models import (
    Commodity, Mandi, DEFAULT_ANNOTATION_CLASSES, EXTRA_CLASS_PALETTE,
)
from annotation.models import (
    Submission, Particle, ParticleLabel, SubmissionStatus,
)
from ml.export import build_coco


class CommodityClassModelTests(TestCase):
    def setUp(self):
        self.c = Commodity.objects.create(code="WHEAT", name="Wheat")

    def test_new_commodity_has_the_five_locked_defaults(self):
        classes = self.c.annotation_classes()
        self.assertEqual([c["value"] for c in classes],
                         ["good", "broken", "foreign", "immature", "fungal"])
        self.assertTrue(all(c["locked"] for c in classes))
        # Exact PRD spec colors preserved.
        self.assertEqual(classes[0]["color"], "#2ECC71")

    def test_extras_get_palette_colors_and_are_unlocked(self):
        self.c.extra_classes = [{"value": "weevil_damaged", "label": "Weevil damaged"}]
        self.c.save()
        classes = self.c.annotation_classes()
        self.assertEqual(len(classes), 6)
        extra = classes[-1]
        self.assertFalse(extra["locked"])
        self.assertEqual(extra["color"], EXTRA_CLASS_PALETTE[0])

    def test_label_validation(self):
        self.c.extra_classes = [{"value": "weevil_damaged", "label": "Weevil damaged"}]
        self.c.save()
        self.assertTrue(self.c.is_valid_label("good"))
        self.assertTrue(self.c.is_valid_label("weevil_damaged"))
        self.assertFalse(self.c.is_valid_label("unlabeled"))   # not assignable
        self.assertFalse(self.c.is_valid_label("nonsense"))

    def test_color_map_includes_unlabeled(self):
        self.assertEqual(self.c.class_color_map()["unlabeled"], "#95A5A6")


class DynamicClassFlowTests(TestCase):
    """Assayer labels with a custom class → QC overrides → COCO export."""

    def setUp(self):
        self.mandi = Mandi.objects.create(name="Test Mandi", district="D", state="S")
        self.commodity = Commodity.objects.create(
            code="RAGI", name="Ragi",
            extra_classes=[{"value": "weevil_damaged", "label": "Weevil damaged"}],
        )
        self.mandi.commodities.add(self.commodity)
        self.assayer = User.objects.create_user(
            username="a1", password="x" * 12, role=Role.ASSAYER)
        self.assayer.mandis.add(self.mandi)
        self.qc = User.objects.create_user(
            username="q1", password="x" * 12, role=Role.QC_REVIEWER)
        self.admin = User.objects.create_user(
            username="ad1", password="x" * 12, role=Role.ADMIN)
        self.sub = Submission.objects.create(
            sample_number=1, assayer=self.assayer,
            commodity=self.commodity, mandi=self.mandi)
        self.p = Particle.objects.create(
            submission=self.sub, particle_id=1,
            polygon=[[0, 0], [10, 0], [10, 10]])

    def test_assayer_can_apply_custom_class(self):
        self.client.force_login(self.assayer)
        res = self.client.post(
            reverse("annotation:label_particle", kwargs={"pk": self.sub.id}),
            json.dumps({"particle_pk": self.p.id, "label": "weevil_damaged"}),
            content_type="application/json")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["label"], "weevil_damaged")
        self.assertEqual(body["color"], EXTRA_CLASS_PALETTE[0])
        self.p.refresh_from_db()
        self.assertEqual(self.p.label, "weevil_damaged")

    def test_label_from_another_commodity_is_rejected(self):
        self.client.force_login(self.assayer)
        res = self.client.post(
            reverse("annotation:label_particle", kwargs={"pk": self.sub.id}),
            json.dumps({"particle_pk": self.p.id, "label": "not_a_class"}),
            content_type="application/json")
        self.assertEqual(res.status_code, 400)

    def test_qc_override_accepts_custom_class(self):
        self.sub.submitted_at = self.sub.created_at
        self.sub.status = SubmissionStatus.PENDING_QC
        self.sub.save()
        self.client.force_login(self.qc)
        res = self.client.post(
            reverse("qc:override_label", kwargs={"pk": self.sub.id}),
            json.dumps({"particle_pk": self.p.id, "label": "weevil_damaged"}),
            content_type="application/json")
        self.assertEqual(res.status_code, 200)
        self.p.refresh_from_db()
        self.assertEqual(self.p.effective_label, "weevil_damaged")

    def test_admin_add_and_remove_class_endpoints(self):
        self.client.force_login(self.admin)
        add_url = reverse("dashboard:commodity_add_class",
                          kwargs={"pk": self.commodity.id})
        # add
        self.client.post(add_url, {"class_name": "Sprouted"})
        self.commodity.refresh_from_db()
        self.assertIn("sprouted",
                      [e["value"] for e in self.commodity.extra_class_list])
        # duplicate rejected (no second copy appears)
        self.client.post(add_url, {"class_name": "Sprouted"})
        self.commodity.refresh_from_db()
        self.assertEqual(
            [e["value"] for e in self.commodity.extra_class_list].count("sprouted"), 1)
        # reserved name rejected
        self.client.post(add_url, {"class_name": "Good grain"})
        self.commodity.refresh_from_db()
        self.assertNotIn("good_grain",
                         [e["value"] for e in self.commodity.extra_class_list])
        # removal blocked while in use
        self.p.label = "weevil_damaged"
        self.p.save()
        rm_url = reverse("dashboard:commodity_remove_class",
                         kwargs={"pk": self.commodity.id})
        self.client.post(rm_url, {"class_value": "weevil_damaged"})
        self.commodity.refresh_from_db()
        self.assertIn("weevil_damaged",
                      [e["value"] for e in self.commodity.extra_class_list])
        # removal allowed once unused
        self.p.label = ParticleLabel.UNLABELED
        self.p.save()
        self.client.post(rm_url, {"class_value": "weevil_damaged"})
        self.commodity.refresh_from_db()
        self.assertNotIn("weevil_damaged",
                         [e["value"] for e in self.commodity.extra_class_list])

    def test_coco_export_includes_custom_category(self):
        self.p.label = "weevil_damaged"
        self.p.save()
        self.sub.status = SubmissionStatus.QC_APPROVED
        self.sub.save()
        coco, included = build_coco(self.commodity)
        self.assertEqual(included, 1)
        names = {c["name"] for c in coco["categories"]}
        self.assertIn("Weevil damaged", names)
        # Defaults keep stable ids 1–5; the extra follows.
        by_name = {c["name"]: c["id"] for c in coco["categories"]}
        self.assertEqual(by_name["Good grain"], 1)
        self.assertEqual(by_name["Weevil damaged"], 6)
        self.assertEqual(coco["annotations"][0]["category_id"], 6)

    def test_qc_review_page_shows_commodity_classes(self):
        self.sub.submitted_at = self.sub.created_at
        self.sub.status = SubmissionStatus.PENDING_QC
        self.sub.save()
        self.client.force_login(self.qc)
        res = self.client.get(reverse("qc:review", kwargs={"pk": self.sub.id}))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Weevil damaged")

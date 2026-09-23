import base64
from io import BytesIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from openai import AsyncOpenAI
from PIL import Image

from ai_grading import (AIConfigurationError, AIGradingError, OUTPUT_TOKEN_LIMIT, RUBRIC_INSTRUCTION, SYSTEM_INSTRUCTION,
                        build_model_input, get_timeout_seconds, grade_assessment)
from document_processing import EmbeddedImage, PDFPageContent
from grading import AssessmentResult, GradingInput, PreparedContent
from main import app
from rubric import demo_rubric


def prepared_input():
    return GradingInput(
        PreparedContent("text", "work.pdf", "application/pdf", "Student answer"),
        None, None, rubric=demo_rubric(4),
    )


class AIGradingTests(unittest.IsolatedAsyncioTestCase):
    def test_prompt_distinguishes_semantic_and_exact_criteria(self):
        self.assertIn("semantically equivalent wording and valid paraphrases", SYSTEM_INSTRUCTION)
        self.assertIn("exact technical criteria", SYSTEM_INSTRUCTION)
        self.assertIn("generally knowledgeable, close, implied, or unrelated", SYSTEM_INSTRUCTION)

    def test_open_ended_reference_examples_are_non_exclusive(self):
        for instruction in (SYSTEM_INSTRUCTION, RUBRIC_INSTRUCTION):
            with self.subTest(instruction=instruction[:30]):
                self.assertIn("open-ended", instruction)
                self.assertIn("likes(mary, food).", instruction)
                self.assertIn("male(ali).", instruction)
        self.assertIn("must be accepted", SYSTEM_INSTRUCTION)
        self.assertIn("examples as non-exclusive", RUBRIC_INSTRUCTION)
        self.assertIn("requested concept and syntax", SYSTEM_INSTRUCTION)

    def test_balanced_grading_policy_preserves_semantic_and_exact_boundaries(self):
        semantic_policy = [
            "demonstrated understanding, not wording similarity",
            "valid paraphrases, including synonyms",
            "multiple correct formulations or examples",
            "not to demand verbatim reproduction",
        ]
        exact_policy = [
            "invalid syntax remains incorrect",
            "explicitly requires that exact example, token, identifier, or notation",
            "technically wrong where exactness matters",
        ]
        for phrase in semantic_policy + exact_policy:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, SYSTEM_INSTRUCTION)

    def test_rubric_normalizer_keeps_reference_wording_out_of_mandatory_points(self):
        for phrase in [
            "REQUIRED MEANING",
            "ACCEPTABLE EXAMPLES",
            "Do not turn every phrase or example in a reference answer into a mandatory marking point",
            "reference example in guidance as non-exclusive",
        ]:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, RUBRIC_INSTRUCTION)

    async def test_default_disabled_even_with_key(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key-not-real"}), patch("ai_grading.AsyncOpenAI") as client:
            with self.assertRaisesRegex(AIConfigurationError, "disabled"):
                await grade_assessment(prepared_input())
            client.assert_not_called()

    async def test_missing_or_placeholder_key(self):
        for key in ["", "your_api_key_here"]:
            with patch.dict(os.environ, {"OPENAI_API_KEY": key}, clear=True), patch("ai_grading.load_dotenv"), patch("ai_grading.AsyncOpenAI") as client:
                with self.assertRaisesRegex(AIConfigurationError, "OPENAI_API_KEY"):
                    await grade_assessment(prepared_input(), allow_api_request=True)
                client.assert_not_called()

    def test_timeout_configuration_default_override_and_invalid_values(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(get_timeout_seconds(), 120.0)
        with patch.dict(os.environ, {"OPENAI_TIMEOUT_SECONDS": "180.5"}, clear=True):
            self.assertEqual(get_timeout_seconds(), 180.5)
        for value in ["0", "-1", "601", "NaN", "Infinity", "invalid"]:
            with self.subTest(value=value), patch.dict(
                    os.environ, {"OPENAI_TIMEOUT_SECONDS": value}, clear=True):
                with self.assertRaisesRegex(AIConfigurationError, "OPENAI_TIMEOUT_SECONDS"):
                    get_timeout_seconds()

    async def test_structured_response_with_mock_transport(self):
        # Fixed fixture exercises the SDK transport and explicit validation; no real request is made.
        fixture = {"summary": "Test fixture", "questions": [{
            "question_id": "q1", "feedback": "Test feedback", "marking_points": [{
                "marking_point_id": "q1_p1",
                "awarded_marks": 2.5, "rationale": "The concept is partly demonstrated.",
                "evidence_status": "found", "evidence": "Student answer", "source_location": "Page 1",
            }],
        }]}

        def handler(request):
            body = json.loads(request.content)
            self.assertFalse(body["store"])
            self.assertEqual(body["model"], "test-model")
            self.assertEqual(body["max_output_tokens"], OUTPUT_TOKEN_LIMIT)
            self.assertNotIn("temperature", body)
            self.assertNotIn("top_p", body)
            self.assertNotIn("reasoning", body)
            schema = body["text"]["format"]["schema"]
            self.assertTrue(body["text"]["format"]["strict"])
            self.assertNotIn("percentage", schema["properties"])
            self.assertNotIn("total_score", schema["properties"])
            self.assertNotIn("max_score", schema["properties"])
            self.assertFalse(schema["additionalProperties"])
            question_schema = schema["$defs"]["QuestionEvaluationDraft"]
            self.assertNotIn("question", question_schema["properties"])
            self.assertNotIn("max_score", question_schema["properties"])
            self.assertIn("marking_points", question_schema["required"])
            point_schema = schema["$defs"]["MarkingPointEvaluationDraft"]
            self.assertNotIn("criterion", point_schema["properties"])
            self.assertNotIn("max_marks", point_schema["properties"])
            self.assertEqual(point_schema["properties"]["evidence_status"]["enum"],
                             ["found", "not_found", "unclear"])
            self.assertNotIn("status", point_schema["properties"])
            self.assertIn("source_location", point_schema["required"])
            return httpx.Response(200, json={
                "id": "resp_mock", "object": "response", "created_at": 0,
                "status": "completed", "model": "test-model",
                "output": [{"id": "msg_mock", "type": "message", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": json.dumps(fixture), "annotations": []}]}],
            })

        client = AsyncOpenAI(api_key="test-key-not-real", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        with patch("ai_grading.get_configuration", return_value=("test-key-not-real", "test-model", 120.0)), patch("ai_grading.AsyncOpenAI", return_value=client):
            result = await grade_assessment(prepared_input(), allow_api_request=True)
        self.assertIsInstance(result.result, AssessmentResult)
        self.assertEqual(result.result.percentage, 62.5)

    async def test_refusal_is_not_an_assessment(self):
        def handler(request):
            return httpx.Response(200, json={"id": "resp_mock", "object": "response", "created_at": 0, "status": "completed", "model": "test-model", "output": [{"id": "msg_mock", "type": "message", "role": "assistant", "status": "completed", "content": [{"type": "refusal", "refusal": "Test refusal"}]}]})

        client = AsyncOpenAI(api_key="test-key-not-real", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        with patch("ai_grading.get_configuration", return_value=("test-key-not-real", "test-model", 120.0)), patch("ai_grading.AsyncOpenAI", return_value=client):
            with self.assertRaises(AIGradingError) as raised:
                await grade_assessment(prepared_input(), allow_api_request=True)
        self.assertEqual(raised.exception.category, "model_refusal")


class InputAndEndpointTests(unittest.TestCase):
    def test_visual_payload_preserves_bytes(self):
        image = EmbeddedImage("work-page-1.jpg", "image/jpeg", b"binary fixture", 10, 10)
        cases = [
            (PreparedContent("image", "work.png", "image/png", original_bytes=b"binary fixture"), "input_image"),
            (PreparedContent("pdf_visual", "work.pdf", "application/pdf",
                             pdf_pages=(PDFPageContent(1, rendered_image=image),)), "input_image"),
        ]
        for content, expected_type in cases:
            parts = build_model_input(GradingInput(content, content, "Additional criteria", rubric=demo_rubric(4)))[0]["content"]
            visual_parts = [part for part in parts if part["type"] == expected_type]
            self.assertEqual(len(visual_parts), 1)
            for part in visual_parts:
                url = part.get("image_url") or part["file_data"]
                self.assertEqual(base64.b64decode(url.split(",", 1)[1]), b"binary fixture")
            self.assertIn("CANONICAL RUBRIC", parts[-1]["text"])

    def test_single_image_has_stable_source_label(self):
        content = PreparedContent("image", "answer.png", "image/png", original_bytes=b"fixture")
        parts = build_model_input(GradingInput(content, None, None, rubric=demo_rubric(1)))[0]["content"]
        self.assertEqual(parts[0]["text"], "STUDENT WORK — IMAGE 1 — answer.png")

    def test_multiple_images_are_ordered_in_one_model_input(self):
        pages = tuple(PreparedContent("image", f"page-{index}.png", "image/png", original_bytes=f"page {index}".encode())
                      for index in range(1, 4))
        prepared = GradingInput(pages[0], None, None, pages, demo_rubric(4))
        model_input = build_model_input(prepared)
        self.assertEqual(len(model_input), 1)
        parts = model_input[0]["content"]
        self.assertEqual(sum(part["type"] == "input_image" for part in parts), 3)
        labels = [part["text"] for part in parts if part["type"] == "input_text"]
        self.assertTrue(any("Grade all pages together as one submission" in label for label in labels))
        for index in range(1, 4):
            self.assertTrue(any(f"PAGE {index} OF 3" in label for label in labels))

    def test_endpoints_work_without_key_and_never_create_ai_client(self):
        image = BytesIO()
        Image.new("RGB", (10, 10), "white").save(image, format="PNG")
        with TemporaryDirectory() as temporary, patch("database.DB_PATH", Path(temporary) / "ai_checker.db"), patch.dict(os.environ, {}, clear=True), patch("assessment_service.load_dotenv"), patch("ai_grading.AsyncOpenAI") as ai_client, TestClient(app) as client:
            self.assertEqual(client.get("/health").json(), {"status": "ok"})
            response = client.post("/api/assess", files={"student_work": ("work.png", image.getvalue(), "image/png")}, data={"criteria_text": "Award 4 marks."})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["grading_mode"], "mock")
            self.assertEqual(response.json()["result"]["total_score"], 17)
            ai_client.assert_not_called()


if __name__ == "__main__":
    unittest.main()

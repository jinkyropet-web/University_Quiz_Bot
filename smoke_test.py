"""Offline smoke test: DATABASE_PATH must point to a disposable SQLite file."""
import json
import tempfile
from pathlib import Path

import database
from services import openai_service
from services.document_processor import extract_text


def run():
    database.init_db()

    class User:
        id = 998877
        username = "smoke"
        first_name = "Smoke Test"

    database.upsert_user(User())
    quiz_id = database.create_quiz("Smoke test", "test", User.id, 30, published=False)
    database.add_question(quiz_id, "2+2?", ["3", "4", "5", "6"], 1, "Four")
    assert database.get_quiz(quiz_id) == (None, [])
    database.set_quiz_published(quiz_id, True)
    quiz, questions = database.get_quiz(quiz_id)
    assert quiz and len(questions) == 1

    class Message:
        content = json.dumps({"questions": [{"question": "سؤال تجريبي؟", "options": ["أ", "ب", "ج", "د"], "correct_index": 1, "explanation": "شرح"}]}, ensure_ascii=False)

    class Choice:
        message = Message()

    class Response:
        choices = [Choice()]

    class Completions:
        def create(self, **kwargs):
            return Response()

    class Chat:
        completions = Completions()

    class FakeClient:
        chat = Chat()

    old_client = openai_service._client
    openai_service._client = lambda: FakeClient()
    try:
        generated = openai_service.generate_mcqs("نص تعليمي تجريبي طويل بما يكفي لاختبار توليد الأسئلة. " * 4, 1)
        assert len(generated) == 1 and generated[0]["correct_index"] == 1
    finally:
        openai_service._client = old_client

    with tempfile.TemporaryDirectory() as folder:
        text_file = Path(folder) / "lesson.txt"
        text_file.write_text("The heart pumps blood through the circulatory system.", encoding="utf-8")
        assert "pumps blood" in extract_text(text_file)

    database.save_attempt(quiz_id, User.id, 1, 1)
    assert database.leaderboard(1)[0]["score"] == 1
    print("PASS: database, draft/publish, mocked OpenAI generation, text extraction, leaderboard")


if __name__ == "__main__":
    run()

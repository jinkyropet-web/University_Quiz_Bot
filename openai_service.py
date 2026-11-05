"""OpenAI integration for generating validated quiz questions and answering students."""
import json
import re
from typing import Any

from config import OPENAI_API_KEY, OPENAI_MODEL


class OpenAIServiceError(RuntimeError):
    """Safe, user-facing error from the AI service."""


def _client():
    if not OPENAI_API_KEY:
        raise OpenAIServiceError("لم يتم ضبط OPENAI_API_KEY في متغيرات البيئة.")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise OpenAIServiceError("مكتبة OpenAI غير مثبتة. ثبّت المتطلبات من requirements.txt.") from exc
    return OpenAI(api_key=OPENAI_API_KEY, timeout=60.0, max_retries=2)


def _parse_json(content: str) -> dict[str, Any]:
    if not content or not content.strip():
        raise OpenAIServiceError("أعاد OpenAI استجابة فارغة. جرّب مرة أخرى أو قلّل حجم النص.")
    cleaned = content.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.I)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise OpenAIServiceError("تعذر قراءة تنسيق الأسئلة الذي أعاده OpenAI. أعد المحاولة.") from exc
    if not isinstance(data, dict):
        raise OpenAIServiceError("صيغة استجابة OpenAI غير صحيحة.")
    return data


def validate_questions(data: dict[str, Any], count: int | None = None) -> list[dict[str, Any]]:
    raw = data.get("questions", [])
    if not isinstance(raw, list):
        raise OpenAIServiceError("لم يُرجع الذكاء الاصطناعي قائمة أسئلة صالحة.")
    result = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question", "")).strip()
        options = item.get("options")
        idx = item.get("correct_index")
        explanation = str(item.get("explanation", "")).strip()
        if not question or not isinstance(options, list) or not 2 <= len(options) <= 6:
            continue
        options = [str(option).strip() for option in options]
        if any(not option for option in options) or not isinstance(idx, int) or isinstance(idx, bool) or not 0 <= idx < len(options):
            continue
        result.append({"question": question, "options": options, "correct_index": idx, "explanation": explanation})
        if count and len(result) >= count:
            break
    if not result:
        raise OpenAIServiceError("لم يتمكن الذكاء الاصطناعي من إنشاء أسئلة صالحة من هذا المحتوى. جرّب ملفًا أو نصًا أوضح.")
    return result


def generate_mcqs(text: str, count: int = 5) -> list[dict[str, Any]]:
    if not isinstance(text, str) or len(text.strip()) < 80:
        raise OpenAIServiceError("المحتوى المستخرج قصير جدًا. ارفع ملفًا يحتوي على نص واضح أو أرسل نصًا أطول.")
    try:
        count = max(1, min(int(count), 30))
    except (TypeError, ValueError):
        count = 5
    prompt = f"""أنشئ {count} سؤال اختيار من متعدد اعتمادًا حصريًا على المادة التالية.
اكتب باللغة نفسها التي كُتبت بها المادة. اجعل لكل سؤال من 4 خيارات متى أمكن، وإجابة صحيحة واحدة.
لا تكرر الأسئلة ولا تضف معلومات من خارج المادة. أعد JSON صالحًا فقط بالشكل التالي:
{{"questions":[{{"question":"نص السؤال","options":["الخيار 1","الخيار 2","الخيار 3","الخيار 4"],"correct_index":0,"explanation":"سبب صحة الإجابة"}}]}}
correct_index رقم يبدأ من صفر. النص:
{text[:24000]}"""
    try:
        response = _client().chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "أنت خبير في إعداد الاختبارات الجامعية. التزم بالمادة المصدر وأخرج JSON فقط."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        content = response.choices[0].message.content if response.choices else ""
        return validate_questions(_parse_json(content), count)
    except OpenAIServiceError:
        raise
    except Exception as exc:
        # Keep provider details in server logs; give a useful, non-secret message to users.
        import logging
        logging.getLogger(__name__).exception("OpenAI question generation failed")
        status = getattr(exc, "status_code", None)
        code = getattr(exc, "code", None)
        if status == 401 or code == "invalid_api_key":
            raise OpenAIServiceError("رفض OpenAI مفتاح API. تحقق من صحة OPENAI_API_KEY وحالة المفتاح.") from exc
        if status == 429:
            raise OpenAIServiceError("وصل حساب OpenAI إلى حد الاستخدام أو لا يوجد رصيد كافٍ. تحقق من الفوترة والحدود.") from exc
        if status in (400, 404):
            raise OpenAIServiceError(f"تعذر تنفيذ الطلب بالنموذج {OPENAI_MODEL}. تحقق من اسم النموذج وصلاحية استخدامه.") from exc
        raise OpenAIServiceError("تعذر الاتصال بخدمة OpenAI. تحقق من اتصال الخادم وإعدادات المفتاح والنموذج، ثم أعد المحاولة.") from exc


def ask_ai(question: str) -> str:
    if not question or not question.strip():
        raise OpenAIServiceError("اكتب سؤالك أولًا.")
    try:
        response = _client().chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "أنت مساعد تعليمي جامعي. أجب بدقة ووضوح، واشرح المصطلحات عند الحاجة."},
                {"role": "user", "content": question[:12000]},
            ],
            temperature=0.3,
        )
        answer = response.choices[0].message.content if response.choices else ""
        return (answer or "لم تصل إجابة من النموذج.").strip()
    except OpenAIServiceError:
        raise
    except Exception as exc:
        import logging
        logging.getLogger(__name__).exception("OpenAI assistant request failed")
        status = getattr(exc, "status_code", None)
        if status == 401:
            raise OpenAIServiceError("مفتاح OpenAI غير صحيح أو غير مفعل.") from exc
        if status == 429:
            raise OpenAIServiceError("تجاوزت حدود استخدام OpenAI أو تحتاج إلى إضافة رصيد.") from exc
        raise OpenAIServiceError("تعذر الاتصال بـ OpenAI حاليًا. تحقق من المفتاح والاتصال واسم النموذج.") from exc

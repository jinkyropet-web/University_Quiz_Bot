import os
import uuid
import logging
from functools import wraps
from flask import Flask, request, redirect, url_for, session, render_template_string, flash, send_from_directory
from werkzeug.utils import secure_filename
from config import FLASK_SECRET_KEY, DASHBOARD_USERNAME, DASHBOARD_PASSWORD, COOKIE_SECURE, UPLOAD_FOLDER, MAX_UPLOAD_MB, DEFAULT_QUESTION_SECONDS
from database import (init_db, dashboard_stats, dashboard_users, dashboard_quizzes,
                      add_material, list_materials, create_quiz, add_question, set_quiz_published, delete_quiz, dashboard_quiz_questions, get_quiz, get_setting, set_setting)
from services.document_processor import extract_text
from services.openai_service import generate_mcqs, OpenAIServiceError

log = logging.getLogger(__name__)
ALLOWED = {".pdf", ".docx", ".pptx", ".xlsx", ".xlsm", ".csv", ".txt", ".png", ".jpg", ".jpeg", ".webp"}
app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=COOKIE_SECURE, MAX_CONTENT_LENGTH=MAX_UPLOAD_MB * 1024 * 1024)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
init_db()

LOGIN = """<!doctype html><html lang=ar dir=rtl><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>دخول الإدارة</title>
<style>body{font-family:Arial;background:#f3f5f9;display:grid;place-items:center;min-height:90vh}.card{background:white;padding:28px;border-radius:16px;width:min(360px,90%);box-shadow:0 8px 30px #0001}input,button{width:100%;padding:12px;margin:8px 0;border:1px solid #ddd;border-radius:9px;box-sizing:border-box}button{background:#173b67;color:white;border:0;cursor:pointer}.msg{color:#a22}</style>
<div class=card><h2>لوحة تحكم البوت</h2><form method=post><input name=username placeholder='اسم المستخدم' required><input name=password type=password placeholder='كلمة المرور' required><button>تسجيل الدخول</button></form><p class=msg>{{msg}}</p></div></html>"""

DASH = """<!doctype html><html lang=ar dir=rtl><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>لوحة التحكم</title>
<style>body{font-family:Arial;background:#f4f6fa;margin:0;color:#172033}.top{background:#173b67;color:white;padding:18px 5%;display:flex;justify-content:space-between}main{max-width:1150px;margin:24px auto;padding:0 16px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}.card,section{background:white;border-radius:13px;padding:18px;box-shadow:0 3px 14px #00008;margin:16px 0}.num{font-size:28px;font-weight:bold;color:#173b67}table{width:100%;border-collapse:collapse}td,th{text-align:right;padding:10px;border-bottom:1px solid #eee}input,button{padding:10px;border:1px solid #ddd;border-radius:8px;margin:3px}button{background:#173b67;color:white;border:0;cursor:pointer}a{color:inherit}.muted{color:#667}.msg{padding:10px;background:#fff5d9;border-radius:8px;margin:8px 0}.inline{display:inline-block}</style>
<header class=top><b>🎓 الإدارة الجامعية</b><a href=/logout>تسجيل الخروج</a></header><main>
{% with messages=get_flashed_messages() %}{% for m in messages %}<div class=msg>{{m}}</div>{% endfor %}{% endwith %}
<div class=grid>{% for k,v in stats.items() %}<div class=card><div>{{k}}</div><div class=num>{{v}}</div></div>{% endfor %}</div>
<section><h3>رفع مادة تعليمية</h3><form action=/upload method=post enctype=multipart/form-data><input name=title placeholder='عنوان المادة' required><input type=file name=file required><button>رفع الملف</button></form><p class=muted>الصيغ المدعومة: PDF, DOCX, PPTX, XLSX, XLSM, CSV, TXT والصور. الحد {{maxmb}} MB.</p></section>
<section><h3>توليد اختبار بالذكاء الاصطناعي</h3><p class=muted>ارفع المادة أولًا، ثم اضغط «توليد أسئلة». سيُنشأ الاختبار كمسودة حتى تراجعه وتنشره.</p><table><tr><th>المادة</th><th>تاريخ الرفع</th><th>الإجراء</th></tr>{% for m in materials %}<tr><td>{{m.title}}</td><td>{{m.created_at}}</td><td><form class=inline action=/generate method=post><input type=hidden name=material_id value={{m.id}}><input name=count type=number min=1 max=30 value=5 style='width:65px'><button>توليد أسئلة</button></form><a href='/materials/{{m.filename}}'>تنزيل</a></td></tr>{% else %}<tr><td colspan=3>لا توجد مواد بعد.</td></tr>{% endfor %}</table></section>
<section><h3>الاختبارات</h3><table><tr><th>رقم</th><th>العنوان</th><th>الأسئلة</th><th>الحالة</th><th>الإجراء</th></tr>{% for q in quizzes %}<tr><td>{{q.id}}</td><td>{{q.title}}</td><td>{{q.question_count}}</td><td>{{'منشور' if q.is_published else 'مسودة'}}</td><td><a href='/review/{{q.id}}'>مراجعة الأسئلة</a> {% if not q.is_published and q.question_count %}<form class=inline method=post action='/publish/{{q.id}}'><button>اعتماد ونشر</button></form>{% endif %}<form class=inline method=post action='/delete/{{q.id}}' onsubmit="return confirm('حذف الاختبار؟')"><button>حذف</button></form></td></tr>{% else %}<tr><td colspan=5>لا توجد اختبارات.</td></tr>{% endfor %}</table></section>
<section><h3>إعدادات العرض</h3><p>المتصدرون: {{'ظاهرون' if leaderboard_enabled else 'مخفيون'}}</p><form method=post action=/toggle-leaderboard><button>{{'إخفاء المتصدرين' if leaderboard_enabled else 'إظهار المتصدرين'}}</button></form></section>
<section><h3>الطلاب</h3><table><tr><th>المعرف</th><th>الاسم</th><th>اسم المستخدم</th><th>الحالة</th></tr>{% for u in users %}<tr><td>{{u.id}}</td><td>{{u.first_name}}</td><td>{{u.username}}</td><td>{{'محظور' if u.is_blocked else 'نشط'}}</td></tr>{% else %}<tr><td colspan=4>لا يوجد طلاب مسجلون بعد.</td></tr>{% endfor %}</table></section>
</main></html>"""


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper


@app.route("/", methods=["GET", "POST"])
def login():
    if session.get("logged_in"):
        return redirect(url_for("dashboard"))
    msg = ""
    if request.method == "POST":
        if not DASHBOARD_PASSWORD:
            msg = "اضبط DASHBOARD_PASSWORD في متغيرات البيئة أولًا."
        elif request.form.get("username") == DASHBOARD_USERNAME and request.form.get("password") == DASHBOARD_PASSWORD:
            session.clear()
            session["logged_in"] = True
            return redirect(url_for("dashboard"))
        else:
            msg = "بيانات الدخول غير صحيحة."
    return render_template_string(LOGIN, msg=msg)


@app.route("/dashboard")
@login_required
def dashboard():
    labels = {"users": "الطلاب", "quizzes": "الاختبارات", "questions": "الأسئلة", "attempts": "المحاولات"}
    stats = dashboard_stats()
    return render_template_string(DASH, stats={labels[k]: v for k, v in stats.items()},
                                  quizzes=dashboard_quizzes(), users=dashboard_users(),
                                  materials=list_materials(), maxmb=MAX_UPLOAD_MB, leaderboard_enabled=get_setting("leaderboard_enabled", "1") == "1")


@app.route("/upload", methods=["POST"])
@login_required
def upload():
    f = request.files.get("file")
    title = request.form.get("title", "").strip()
    if not f or not f.filename or not title:
        flash("يرجى اختيار ملف وكتابة عنوان.")
        return redirect(url_for("dashboard"))
    original_ext = os.path.splitext(f.filename)[1].lower()
    if original_ext not in ALLOWED:
        flash("صيغة الملف غير مدعومة.")
        return redirect(url_for("dashboard"))
    filename = secure_filename(f.filename)
    if not filename or os.path.splitext(filename)[1].lower() != original_ext:
        filename = f"material_{uuid.uuid4().hex}{original_ext}"
    stored = filename
    target = os.path.join(UPLOAD_FOLDER, stored)
    root, suffix = os.path.splitext(stored)
    n = 1
    while os.path.exists(target):
        stored = f"{root}_{n}{suffix}"
        target = os.path.join(UPLOAD_FOLDER, stored)
        n += 1
    f.save(target)
    add_material(title, stored)
    flash("تم رفع المادة وحفظها. يمكنك الآن توليد الأسئلة منها.")
    return redirect(url_for("dashboard"))


@app.route("/generate", methods=["POST"])
@login_required
def generate_from_material():
    material_id = request.form.get("material_id", type=int)
    count = request.form.get("count", default=5, type=int)
    material = next((m for m in list_materials(limit=500) if m["id"] == material_id), None)
    if not material:
        flash("لم يتم العثور على المادة المطلوبة.")
        return redirect(url_for("dashboard"))
    path = os.path.join(UPLOAD_FOLDER, material["filename"])
    try:
        extracted = extract_text(path)
        if not extracted or len(extracted.strip()) < 80:
            raise ValueError("لم يتم استخراج نص كافٍ من الملف. تحقق من أن PDF ليس صورًا ممسوحة أو ثبّت OCR.")
        questions = generate_mcqs(extracted, count=count or 5)
        quiz_id = create_quiz(f"اختبار: {material['title']}", f"مولّد آليًا من المادة: {material['title']}", 0,
                              DEFAULT_QUESTION_SECONDS, published=False)
        for q in questions:
            add_question(quiz_id, q["question"], q["options"], q["correct_index"], q.get("explanation", ""))
        flash(f"تم توليد {len(questions)} أسئلة بنجاح. الاختبار رقم {quiz_id} محفوظ كمسودة؛ راجعه ثم اضغط اعتماد ونشر.")
    except (OpenAIServiceError, ValueError, OSError) as exc:
        flash(str(exc))
    except Exception:
        log.exception("Document question generation failed")
        flash("حدث خطأ أثناء معالجة المادة. راجع سجل التشغيل لمعرفة التفاصيل.")
    return redirect(url_for("dashboard"))


@app.route("/review/<int:quiz_id>")
@login_required
def review_quiz(quiz_id):
    quiz, _ = get_quiz(quiz_id)
    # get_quiz intentionally hides drafts from students; fetch the draft title directly from admin data.
    record = next((q for q in dashboard_quizzes(limit=500) if q["id"] == quiz_id), None)
    if not record:
        flash("الاختبار غير موجود.")
        return redirect(url_for("dashboard"))
    questions = dashboard_quiz_questions(quiz_id)
    html = """<!doctype html><html lang=ar dir=rtl><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>مراجعة الاختبار</title>
    <style>body{font-family:Arial;background:#f4f6fa;margin:24px;color:#172033}.card{background:white;padding:18px;border-radius:12px;margin:12px auto;max-width:850px}button{padding:10px 16px;background:#173b67;color:white;border:0;border-radius:8px}a{color:#173b67}</style>
    <div class=card><a href=/dashboard>العودة للوحة التحكم</a><h2>{{quiz.title}}</h2><p>الحالة: {{'منشور' if quiz.is_published else 'مسودة'}}</p></div>
    {% for q in questions %}<div class=card><b>س{{loop.index}}: {{q.question}}</b><ol>{% for opt in q.options %}<li>{{opt}} {% if loop.index0 == q.correct_index %}— <b>الإجابة الصحيحة</b>{% endif %}</li>{% endfor %}</ol><p>{{q.explanation}}</p></div>{% else %}<div class=card>لا توجد أسئلة في هذا الاختبار.</div>{% endfor %}
    {% if not quiz.is_published and questions %}<div class=card><form method=post action='/publish/{{quiz.id}}'><button>اعتماد الاختبار ونشره</button></form></div>{% endif %}</html>"""
    import json
    for item in questions:
        item["options"] = json.loads(item["options_json"])
    return render_template_string(html, quiz=record, questions=questions)


@app.route("/publish/<int:quiz_id>", methods=["POST"])
@login_required
def publish_quiz(quiz_id):
    set_quiz_published(quiz_id, True)
    flash("تم نشر الاختبار. أصبح ظاهرًا في قائمة اختبارات الطلاب.")
    return redirect(url_for("dashboard"))


@app.route("/delete/<int:quiz_id>", methods=["POST"])
@login_required
def remove_quiz(quiz_id):
    delete_quiz(quiz_id)
    flash("تم حذف الاختبار.")
    return redirect(url_for("dashboard"))


@app.route("/toggle-leaderboard", methods=["POST"])
@login_required
def toggle_leaderboard():
    current = get_setting("leaderboard_enabled", "1") == "1"
    set_setting("leaderboard_enabled", "0" if current else "1")
    flash("تم تحديث إعداد عرض المتصدرين.")
    return redirect(url_for("dashboard"))


@app.route("/materials/<path:filename>")
@login_required
def material_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename, as_attachment=True)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))

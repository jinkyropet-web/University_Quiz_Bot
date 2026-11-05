import os, random, logging, asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (Application, CommandHandler, MessageHandler, CallbackQueryHandler,
                          ConversationHandler, ContextTypes, filters)
from config import BOT_TOKEN, ADMIN_IDS, DEFAULT_QUESTION_SECONDS, UPLOAD_FOLDER
from database import (init_db, upsert_user, create_quiz, add_question, get_quiz,
                      list_quizzes, save_attempt, list_materials, add_material, set_quiz_published, leaderboard, get_setting)
from services.document_processor import extract_text
from services.openai_service import generate_mcqs, ask_ai, OpenAIServiceError

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)
TITLE, DESCRIPTION, QUESTION, OPTIONS, CORRECT, EXPLANATION, NEXT_STEP = range(7)

def is_admin(uid):
    return uid in ADMIN_IDS

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    upsert_user(update.effective_user)
    keyboard = [
        ["📚 المكتبة التعليمية", "📝 الاختبارات"],
        ["➕ إنشاء اختبار", "🤖 المساعد الذكي"],
        ["🏆 المتصدرون", "ℹ️ المساعدة"],
    ]
    if is_admin(update.effective_user.id):
        keyboard.append(["⚙️ لوحة المشرف"])
    await update.message.reply_text(
        "أهلًا بك في البوت الجامعي الذكي 🎓\nاختر الخدمة التي تريدها:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )

async def help_cmd(update, context):
    await update.message.reply_text(
        "/start — القائمة الرئيسية\n/quizzes — عرض الاختبارات\n/quiz رقم — بدء اختبار\n"
        "/newquiz — إنشاء اختبار (للمشرفين)\n/cancel — إلغاء العملية"
    )

async def quizzes_cmd(update, context):
    items = list_quizzes()
    if not items:
        await update.message.reply_text("لا توجد اختبارات منشورة حاليًا.")
        return
    rows = [[InlineKeyboardButton(f"{q['title']} ({q['question_count']} سؤال)", callback_data=f"open:{q['id']}")] for q in items]
    await update.message.reply_text("الاختبارات المتاحة:", reply_markup=InlineKeyboardMarkup(rows))

async def open_quiz(update, context):
    old_task = context.user_data.pop("question_timer_task", None)
    if old_task and not old_task.done(): old_task.cancel()
    query = update.callback_query
    await query.answer()
    quiz_id = int(query.data.split(":")[1])
    quiz, questions = get_quiz(quiz_id)
    if not quiz or not questions:
        await query.edit_message_text("الاختبار غير موجود أو لا يحتوي أسئلة.")
        return
    context.user_data["active_quiz"] = {"id": quiz_id, "questions": questions, "index": 0, "score": 0, "user_id": query.from_user.id, "question_seconds": quiz.get("question_seconds", DEFAULT_QUESTION_SECONDS)}
    await send_question(query.message, context)

async def send_question(message, context):
    state = context.user_data["active_quiz"]
    i = state["index"]
    if i >= len(state["questions"]):
        old_task = context.user_data.pop("question_timer_task", None)
        if old_task and old_task is not asyncio.current_task() and not old_task.done(): old_task.cancel()
        score, total, qid = state["score"], len(state["questions"]), state["id"]
        if total:
            save_attempt(qid, state.get("user_id", message.chat_id), score, total)
            result_text = f"انتهى الاختبار! نتيجتك: {score}/{total} ({round(score/total*100)}%)."
        else:
            result_text = "هذا الاختبار لا يحتوي على أسئلة بعد."
        await message.reply_text(result_text)
        context.user_data.pop("active_quiz", None)
        return
    q = state["questions"][i]
    import json
    opts = json.loads(q["options_json"])
    if state.get("shuffle"):
        random.shuffle(opts)
    keyboard = [[InlineKeyboardButton(f"{n+1}. {v}", callback_data=f"answer:{i}:{n}")]
               for n, v in enumerate(opts)]
    context.user_data["current_options"] = opts
    context.user_data["current_correct"] = opts.index(opts[q["correct_index"]]) if opts else 0
    sent = await message.reply_text(f"السؤال {i+1}/{len(state['questions'])}\n(الوقت: {state.get('question_seconds', DEFAULT_QUESTION_SECONDS)} ثانية)\n\n{q['question']}",
                                    reply_markup=InlineKeyboardMarkup(keyboard))
    task = asyncio.create_task(_question_timeout(context, sent, i, state.get("question_seconds", DEFAULT_QUESTION_SECONDS)))
    context.user_data["question_timer_task"] = task

async def _question_timeout(context, message, expected_index, seconds):
    try:
        await asyncio.sleep(max(5, int(seconds)))
        state = context.user_data.get("active_quiz")
        if not state or state.get("index") != expected_index:
            return
        q = state["questions"][expected_index]
        import json
        options = json.loads(q["options_json"])
        correct_text = options[q["correct_index"]]
        state["index"] += 1
        await message.edit_text(f"⏰ انتهى الوقت!\nالإجابة الصحيحة: {correct_text}")
        await send_question(message, context)
    except asyncio.CancelledError:
        return
    except Exception:
        log.exception("Question timer failed")

async def answer_question(update, context):
    query = update.callback_query
    state = context.user_data.get("active_quiz")
    if not state:
        await query.answer()
        await query.edit_message_text("انتهت جلسة الاختبار. ابدأ اختبارًا جديدًا.")
        return
    _, qi, oi = query.data.split(":")
    if int(qi) != state["index"]:
        await query.answer("هذا السؤال لم يعد نشطًا.", show_alert=True)
        return
    await query.answer()
    timer_task = context.user_data.pop("question_timer_task", None)
    if timer_task and not timer_task.done(): timer_task.cancel()
    q = state["questions"][state["index"]]
    import json
    options = context.user_data.get("current_options", json.loads(q["options_json"]))
    # Map the selected displayed option back to the stored option index.
    selected_text = options[int(oi)]
    original = json.loads(q["options_json"])
    correct_text = original[q["correct_index"]]
    correct = selected_text == correct_text
    if correct:
        state["score"] += 1
    explanation = q.get("explanation") or ""
    result = "✅ إجابة صحيحة" if correct else f"❌ إجابة خاطئة\nالإجابة الصحيحة: {correct_text}"
    if explanation:
        result += f"\n\nالتوضيح: {explanation}"
    state["index"] += 1
    await query.edit_message_text(result)
    await send_question(query.message, context)

async def newquiz_start(update, context):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("هذه الوظيفة متاحة للمشرفين فقط.")
        return ConversationHandler.END
    await update.message.reply_text("أرسل عنوان الاختبار:")
    return TITLE

async def newquiz_title(update, context):
    context.user_data["newquiz_title"] = update.message.text[:180]
    await update.message.reply_text("أرسل وصف الاختبار، أو اكتب - لتجاوزه:")
    return DESCRIPTION

async def newquiz_description(update, context):
    desc = "" if update.message.text.strip() == "-" else update.message.text[:1000]
    qid = create_quiz(context.user_data["newquiz_title"], desc, update.effective_user.id, DEFAULT_QUESTION_SECONDS, published=False)
    context.user_data["newquiz_id"] = qid
    context.user_data["question_count"] = 0
    await update.message.reply_text(f"تم إنشاء الاختبار رقم {qid}.\nأرسل نص السؤال الأول:")
    return QUESTION

async def newquiz_question(update, context):
    context.user_data["new_question"] = update.message.text[:1500]
    await update.message.reply_text("أرسل الخيارات في رسالة واحدة، كل خيار في سطر. (2 إلى 6 خيارات)")
    return OPTIONS

async def newquiz_options(update, context):
    options = [x.strip() for x in update.message.text.splitlines() if x.strip()]
    if not 2 <= len(options) <= 6:
        await update.message.reply_text("أرسل من خيارين إلى 6 خيارات، كل خيار في سطر.")
        return OPTIONS
    context.user_data["new_options"] = options
    lines = "\n".join(f"{i+1}. {x}" for i, x in enumerate(options))
    await update.message.reply_text(f"{lines}\n\nأرسل رقم الإجابة الصحيحة:")
    return CORRECT

async def newquiz_correct(update, context):
    try:
        idx = int(update.message.text) - 1
        if idx < 0 or idx >= len(context.user_data["new_options"]): raise ValueError
    except ValueError:
        await update.message.reply_text("رقم غير صحيح. أرسل رقم الخيار الصحيح.")
        return CORRECT
    context.user_data["new_correct"] = idx
    await update.message.reply_text("أرسل شرح الإجابة، أو - لتجاوزه:")
    return EXPLANATION

async def newquiz_explanation(update, context):
    explanation = "" if update.message.text.strip() == "-" else update.message.text[:1500]
    add_question(context.user_data["newquiz_id"], context.user_data["new_question"],
                 context.user_data["new_options"], context.user_data["new_correct"], explanation)
    context.user_data["question_count"] += 1
    keyboard = [["➕ سؤال آخر", "✅ إنهاء الاختبار"]]
    await update.message.reply_text(
        f"تم حفظ السؤال. عدد الأسئلة: {context.user_data['question_count']}",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    return NEXT_STEP

async def add_another_or_finish(update, context):
    text = update.message.text.strip()
    if text == "➕ سؤال آخر":
        await update.message.reply_text("أرسل نص السؤال التالي:")
        return QUESTION
    if text == "✅ إنهاء الاختبار":
        set_quiz_published(context.user_data.get("newquiz_id"), True)
        await update.message.reply_text(
            f"اكتمل إنشاء الاختبار رقم {context.user_data.get('newquiz_id')}.\n"
            "يمكن للطلاب الوصول إليه من قائمة الاختبارات.",
            reply_markup=ReplyKeyboardMarkup([["📚 المكتبة التعليمية", "📝 الاختبارات"]], resize_keyboard=True)
        )
        for key in list(context.user_data):
            if key.startswith("new") or key == "question_count":
                context.user_data.pop(key, None)
        return ConversationHandler.END
    await update.message.reply_text("اختر: سؤال آخر أو إنهاء الاختبار.")
    return NEXT_STEP

async def cancel(update, context):
    timer_task = context.user_data.pop("question_timer_task", None)
    if timer_task and not timer_task.done(): timer_task.cancel()
    context.user_data.pop("active_quiz", None)
    await update.message.reply_text("تم إلغاء العملية أو الاختبار الجاري.")
    return ConversationHandler.END

async def text_router(update, context):
    text = update.message.text or ""
    if text == "📝 الاختبارات":
        await quizzes_cmd(update, context)
    elif text == "📚 المكتبة التعليمية":
        mats = list_materials()
        if not mats:
            await update.message.reply_text("المكتبة فارغة حاليًا.")
        else:
            await update.message.reply_text("المواد:\n" + "\n".join(f"• {m['title']}" for m in mats))
    elif text == "🤖 المساعد الذكي":
        await update.message.reply_text("أرسل /ask ثم سؤالك. المساعد متاح عند ضبط OPENAI_API_KEY.")
    elif text == "⚙️ لوحة المشرف":
        if is_admin(update.effective_user.id):
            await update.message.reply_text("لوحة المشرف على الويب: افتح رابط موقع HOKAS بعد النشر.")
        else:
            await update.message.reply_text("غير مصرح.")
    elif text == "🏆 المتصدرون":
        if get_setting("leaderboard_enabled", "1") != "1":
            await update.message.reply_text("عرض المتصدرين معطّل حاليًا من إدارة البوت.")
        else:
            rows = leaderboard(10)
            if not rows:
                await update.message.reply_text("لا توجد نتائج كافية لعرض المتصدرين بعد.")
            else:
                lines = [f"{i}. {r['first_name'] or r['username'] or r['id']} — {r['score']} نقطة" for i, r in enumerate(rows, 1)]
                await update.message.reply_text("🏆 المتصدرون\n\n" + "\n".join(lines))
    elif text == "ℹ️ المساعدة":
        await help_cmd(update, context)
    else:
        await update.message.reply_text("لم أفهم الطلب. استخدم /start لعرض القائمة.")

async def ask(update, context):
    if not context.args:
        await update.message.reply_text("اكتب سؤالك بعد الأمر، مثال: /ask اشرح cardiac looping")
        return
    try:
        answer = ask_ai(" ".join(context.args))
        # Telegram message text has a 4096-character limit.
        for offset in range(0, len(answer), 3900):
            await update.message.reply_text(answer[offset:offset + 3900])
    except OpenAIServiceError as exc:
        await update.message.reply_text(str(exc))
    except Exception:
        log.exception("AI assistant request failed")
        await update.message.reply_text("تعذر تنفيذ الطلب. راجع سجل التشغيل وإعدادات OpenAI.")

async def receive_document(update, context):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("رفع الملفات وتوليد الاختبارات متاحان للمشرفين فقط.")
        return
    doc = update.message.document
    if not doc:
        return
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    original_name = os.path.basename(doc.file_name or f"file_{doc.file_id}")
    safe_name = "".join(ch for ch in original_name if ch.isalnum() or ch in "._- ").strip() or f"file_{doc.file_id}"
    path = os.path.join(UPLOAD_FOLDER, f"{doc.file_unique_id}_{safe_name}")
    try:
        tg_file = await doc.get_file()
        await tg_file.download_to_drive(path)
        add_material(os.path.splitext(safe_name)[0], os.path.basename(path), update.effective_user.id)
        await update.message.reply_text("تم استلام الملف. جارٍ استخراج النص وإنشاء الأسئلة…")
        extracted = extract_text(path)
        questions = generate_mcqs(extracted, count=5)
        quiz_id = create_quiz(f"اختبار: {os.path.splitext(safe_name)[0]}", "مولّد آليًا من ملف مرفوع", update.effective_user.id,
                              DEFAULT_QUESTION_SECONDS, published=False)
        for item in questions:
            add_question(quiz_id, item["question"], item["options"], item["correct_index"], item.get("explanation", ""))
        await update.message.reply_text(
            f"تم إنشاء {len(questions)} أسئلة من الملف بنجاح.\nرقم الاختبار: {quiz_id}\n"
            "حُفظ الاختبار كمسودة غير منشورة. افتح لوحة المشرف لمراجعته واعتماده ونشره."
        )
    except (OpenAIServiceError, ValueError, OSError) as exc:
        log.exception("Uploaded document processing failed")
        await update.message.reply_text(f"تعذرت معالجة الملف: {exc}")
    except Exception:
        log.exception("Uploaded document processing failed")
        await update.message.reply_text("حدث خطأ أثناء معالجة الملف. راجع سجل التشغيل وتأكد من تثبيت متطلبات الصيغة.")

def build_application():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN غير مضبوط.")
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler("newquiz", newquiz_start), MessageHandler(filters.Regex("^➕ إنشاء اختبار$"), newquiz_start)],
        states={
            TITLE:[MessageHandler(filters.TEXT & ~filters.COMMAND,newquiz_title)],
            DESCRIPTION:[MessageHandler(filters.TEXT & ~filters.COMMAND,newquiz_description)],
            QUESTION:[MessageHandler(filters.TEXT & ~filters.COMMAND,newquiz_question)],
            OPTIONS:[MessageHandler(filters.TEXT & ~filters.COMMAND,newquiz_options)],
            CORRECT:[MessageHandler(filters.TEXT & ~filters.COMMAND,newquiz_correct)],
            EXPLANATION:[MessageHandler(filters.TEXT & ~filters.COMMAND,newquiz_explanation)],
            NEXT_STEP:[MessageHandler(filters.TEXT & ~filters.COMMAND,add_another_or_finish)],
        },
        fallbacks=[CommandHandler("cancel",cancel)],
        allow_reentry=True,
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("quizzes", quizzes_cmd))
    app.add_handler(CommandHandler("ask", ask))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("quiz", quiz_command))
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(open_quiz, pattern=r"^open:\d+$"))
    app.add_handler(CallbackQueryHandler(answer_question, pattern=r"^answer:\d+:\d+$"))
    app.add_handler(MessageHandler(filters.Document.ALL, receive_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    return app

async def quiz_command(update, context):
    old_task = context.user_data.pop("question_timer_task", None)
    if old_task and not old_task.done(): old_task.cancel()
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("استخدم: /quiz رقم_الاختبار")
        return
    qid = int(context.args[0])
    quiz, questions = get_quiz(qid)
    if not quiz or not questions:
        await update.message.reply_text("الاختبار غير موجود.")
        return
    context.user_data["active_quiz"] = {"id": qid, "questions": questions, "index": 0, "score": 0, "user_id": update.effective_user.id, "question_seconds": quiz.get("question_seconds", DEFAULT_QUESTION_SECONDS)}
    await send_question(update.message, context)

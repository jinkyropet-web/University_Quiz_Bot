import threading, os
from dashboard.app import app as flask_app
from handlers.bot import build_application
from config import PORT

def run_web():
    flask_app.run(host="0.0.0.0", port=PORT, use_reloader=False)

def main():
    web = threading.Thread(target=run_web, daemon=True)
    web.start()
    application = build_application()
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

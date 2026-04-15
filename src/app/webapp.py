import os
import threading
import time
import webbrowser

from flask import Flask, Response, jsonify, render_template, request

from ..core.config import DEFAULT_CONFIG, HOST, PORT, TEMPLATE_DIR
from .service import GetGifService


service = GetGifService()


def create_app() -> Flask:
    app = Flask(__name__, template_folder=str(TEMPLATE_DIR))

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/api/config", methods=["GET"])
    def get_config():
        return jsonify(service.load_config())

    @app.route("/api/start", methods=["POST"])
    def start_task():
        data = request.get_json(silent=True) or {}
        source_dir = data.get("source_dir", "").strip()
        output_dir = data.get("output_dir", "").strip()
        scan_id = data.get("scan_id", "").strip()

        service.save_config({key: data.get(key, DEFAULT_CONFIG[key]) for key in DEFAULT_CONFIG})

        if not source_dir or not output_dir:
            return jsonify({"error": "请提供源目录和输出目录"}), 400

        if not os.path.isdir(source_dir):
            return jsonify({"error": f"源目录不存在: {source_dir}"}), 400

        return jsonify(service.create_task(source_dir, output_dir, data, scan_id))

    @app.route("/api/heartbeat", methods=["POST"])
    def heartbeat():
        data = request.get_json(silent=True) or {}
        return jsonify({"ok": True, "status": service.heartbeat(data.get("task_id", ""))})

    @app.route("/api/status/<task_id>")
    def task_status(task_id):
        status = service.get_task_status(task_id)
        if status is None:
            return jsonify({"error": "任务不存在"}), 404
        return jsonify(status)

    @app.route("/api/tasks", methods=["GET"])
    def list_tasks():
        return jsonify(service.list_task_dashboard())

    @app.route("/api/logs/<task_id>", methods=["GET"])
    def export_logs(task_id):
        payload = service.get_task_log_text(task_id)
        if payload is None:
            return jsonify({"error": "任务不存在"}), 404
        text, filename = payload
        return Response(
            text,
            mimetype="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename=\"{filename}\"'},
        )

    @app.route("/api/cancel", methods=["POST"])
    def cancel_task():
        data = request.get_json(silent=True) or {}
        if service.cancel_task(data.get("task_id", "")):
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": "任务不存在"}), 404

    @app.route("/api/scan", methods=["POST"])
    def scan_videos():
        data = request.get_json(silent=True) or {}
        source_dir = data.get("source_dir", "").strip()
        if not source_dir or not os.path.isdir(source_dir):
            return jsonify({"error": "目录不存在"}), 400
        return jsonify(service.scan_videos(source_dir))

    @app.route("/api/browse", methods=["POST"])
    def browse_directory():
        try:
            return jsonify({"directory": service.browse_directory()})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/open_folder", methods=["POST"])
    def open_folder():
        data = request.get_json(silent=True) or {}
        if service.open_folder(data.get("path", "").strip()):
            return jsonify({"ok": True})
        return jsonify({"error": "目录不存在"}), 400

    return app


def run_app(app: Flask | None = None):
    app = app or create_app()
    url = f"http://{HOST}:{PORT}"

    print("═" * 50)
    print("  GetGif - 视频批量转GIF工具")
    print(f"  访问 {url}")
    print("═" * 50)

    def open_browser():
        time.sleep(1)
        webbrowser.open(url)

    threading.Thread(target=open_browser, daemon=True).start()
    app.run(debug=False, host=HOST, port=PORT)

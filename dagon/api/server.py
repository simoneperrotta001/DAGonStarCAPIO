from flask import Flask, request
from flask import jsonify
from threading import Thread
from flask_api import status
from werkzeug.serving import make_server


class WorkflowServer(Thread):

    def __init__(self, workflow, port):
        Thread.__init__(self)
        self.workflow = workflow
        self.port = port
        self.configure_server()

    def run(self):
        self.srv.serve_forever()

    def shutdown(self):
        self.srv.shutdown()

    def configure_server(self):
        app = Flask(__name__)

        ######### FLASK FUNCTIONS ##################

        @app.route('/api/<task>/info', methods=['POST'])
        def info(task):
            if not request.is_json:
                return jsonify({"status": "error", "message": "Invalid JSON format"}), status.HTTP_400_BAD_REQUEST
            data = request.json
            task = self.workflow.find_task_by_name(task)
            if task is not None:
                task.set_info(data)
            return jsonify(data)

        ############################################

        self.srv = make_server('127.0.0.1', self.port, app)
        self.ctx = app.app_context()
        self.ctx.push()
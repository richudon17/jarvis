from flask import Flask, render_template, request, jsonify
import uuid

from core.orchestrator import Orchestrator

app = Flask(__name__)

agent = Orchestrator()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/run_goal", methods=["POST"])
def run_goal():
    try:
        data = request.get_json()
        goal = data.get("goal", "").strip() if data else ""
        if not goal:
            return jsonify({"error": "No goal provided"}), 400
        goal_id = str(uuid.uuid4())[:8]
        try:
            result = agent.run(goal)
        except Exception as e:
            return jsonify({
                "error": f"Agent execution failed: {str(e)}",
                "goal": goal
            }), 200
        try:
            trace = getattr(agent, "last_trace", None)
        except Exception:
            trace = None
        return jsonify({
            "goal_id": goal_id,
            "goal": goal,
            "result": result,
            "trace": trace
        })
    except Exception as e:
        return jsonify({
            "error": f"Request processing failed: {str(e)}"
        }), 200


if __name__ == "__main__":
    app.run(debug=True, port=5000)
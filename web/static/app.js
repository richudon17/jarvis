async function runGoal() {
    const goal = document.getElementById("goalInput").value;
    const output = document.getElementById("output");

    output.textContent = "Running...";

    const res = await fetch("/api/run_goal", {
        method: "POST",
        headers: {
            "Content-Type": "application/json"
        },
        body: JSON.stringify({ goal })
    });

    const data = await res.json();
    output.textContent = JSON.stringify(data.result, null, 2);
    document.getElementById("trace").textContent =
        JSON.stringify(data.trace, null, 2);
}
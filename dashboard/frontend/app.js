const logContainer = document.getElementById('logs');
const statusBadge = document.getElementById('status-badge');
const appDisplay = document.getElementById('current-app');
const windowDisplay = document.getElementById('window-title');

const socket = new WebSocket('ws://localhost:8000/ws/dashboard');

socket.onopen = () => {
    statusBadge.innerText = "ONLINE";
    statusBadge.className = "px-3 py-1 rounded-full text-xs bg-green-900/30 text-green-400 border border-green-800";
    addLog("System", "WebSocket Connection Established");
};

socket.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    
    // 1. Update Context UI
    if (msg.event_type === "context_snapshot_ready") {
        appDisplay.innerText = msg.data.app_type.toUpperCase();
        windowDisplay.innerText = msg.data.window_title;
    }

    // 2. Add to Log Feed
    addLog(msg.source, `${msg.event_type}: ${JSON.stringify(msg.data)}`);
};

socket.onclose = () => {
    statusBadge.innerText = "OFFLINE";
    statusBadge.className = "px-3 py-1 rounded-full text-xs bg-red-900/30 text-red-400 border border-red-800";
};

function addLog(source, text) {
    const entry = document.createElement('div');
    entry.className = "border-l-2 border-zinc-800 pl-3 py-1";
    entry.innerHTML = `<span class="text-blue-500 font-bold">[${source.toUpperCase()}]</span> <span class="text-zinc-300">${text}</span>`;
    logContainer.appendChild(entry);
    logContainer.scrollTop = logContainer.scrollHeight;
}

function clearLogs() {
    logContainer.innerHTML = '';
}
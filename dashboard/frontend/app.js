const logContainer = document.getElementById('logs');
const statusBadge = document.getElementById('status-badge');
const appDisplay = document.getElementById('current-app');
const windowDisplay = document.getElementById('window-title');

const socket = new WebSocket('ws://localhost:8000/ws/dashboard');

// Track state to prevent duplicate logs
let lastTitle = "";

socket.onopen = () => {
    statusBadge.innerText = "ONLINE";
    statusBadge.className = "px-3 py-1 rounded-full text-xs bg-green-900/30 text-green-400 border border-green-800";
    addLog("System", "WebSocket Connection Established");
};

socket.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    
    if (msg.event_type === "context_snapshot_ready") {
        const currentTitle = msg.data.window_title || "Unknown Window";
        const currentApp = (msg.data.app_type || "Detecting...").toUpperCase();

        // 🛡️ THE FIX: Only update the UI if it's a REAL window
        if (currentTitle !== "Unknown Linux Window" && currentTitle !== "") {
            
            // Update the display boxes
            appDisplay.innerText = currentApp;
            windowDisplay.innerText = currentTitle;

            // Log if it's a new window
            if (currentTitle !== lastTitle) {
                addLog(msg.source, `Focus changed to: ${currentTitle}`);
                lastTitle = currentTitle;
            }
        }
    } else {
        addLog(msg.source, `${msg.event_type}: ${JSON.stringify(msg.data)}`);
    }
};

socket.onclose = () => {
    statusBadge.innerText = "OFFLINE";
    statusBadge.className = "px-3 py-1 rounded-full text-xs bg-red-900/30 text-red-400 border border-red-800";
};

function addLog(source, text) {
    const entry = document.createElement('div');
    entry.className = "border-l-2 border-zinc-800 pl-3 py-1 mb-1";
    entry.innerHTML = `<span class="text-blue-500 font-bold">[${source.toUpperCase()}]</span> <span class="text-zinc-300">${text}</span>`;
    logContainer.appendChild(entry);
    logContainer.scrollTop = logContainer.scrollHeight;
}

function clearLogs() {
    logContainer.innerHTML = '';
}
const eventLog = document.getElementById('event-log');
const historyList = document.getElementById('history-list');
const statusBadge = document.getElementById('connection-status');

// 1. Connect to WebSocket for Real-time Events
const ws = new WebSocket('ws://localhost:8000/ws/dashboard');

ws.onopen = () => {
    statusBadge.innerText = "CONNECTED";
    statusBadge.className = "status-online";
    console.log("Connected to Operonix Event Bridge");
};

ws.onmessage = (event) => {
    const message = JSON.parse(event.data);
    addEventToLog(message);
};

ws.onclose = () => {
    statusBadge.innerText = "DISCONNECTED";
    statusBadge.className = "status-offline";
};

// 2. Add Live Events to the UI
function addEventToLog(event) {
    const entry = document.createElement('div');
    entry.className = `log-entry ${event.event_type}`;
    
    // Highlight window changes
    let dataDisplay = JSON.stringify(event.data);
    if(event.event_type === "context_snapshot_ready") {
        dataDisplay = `📂 <b>${event.data.app_type}</b>: ${event.data.window_title}`;
    }

    entry.innerHTML = `
        <span class="timestamp">${new Date().toLocaleTimeString()}</span>
        <span class="source">[${event.source}]</span>
        <span class="content">${dataDisplay}</span>
    `;
    eventLog.prepend(entry);
}

// 3. Fetch Persistent History from API
async function fetchHistory() {
    try {
        const response = await fetch('http://localhost:8000/api/actions/history?limit=20');
        const data = await response.json();
        
        historyList.innerHTML = ''; // Clear current view
        data.actions.forEach(action => {
            const item = document.createElement('div');
            item.className = 'history-item';
            item.innerHTML = `
                <span class="time">${action.timestamp}</span>
                <span class="title">${action.data.window_title}</span>
            `;
            historyList.appendChild(item);
        });
    } catch (err) {
        console.error("Failed to fetch history:", err);
    }
}

// Initial Load
fetchHistory();
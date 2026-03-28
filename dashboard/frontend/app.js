const eventLog = document.getElementById('event-log');
const historyList = document.getElementById('history-list');
const statusBadge = document.getElementById('connection-status');

// Track state to prevent spamming
let lastWindowTitle = ""; 

const ws = new WebSocket('ws://localhost:8000/ws/dashboard');

ws.onopen = () => {
    statusBadge.innerText = "ONLINE";
    statusBadge.className = "status-online";
};

ws.onmessage = (event) => {
    const message = JSON.parse(event.data);
    
    if (message.event_type === "context_snapshot_ready") {
        const currentTitle = message.data.window_title;
        
        // ONLY update if it's a real change
        if (currentTitle !== lastWindowTitle) {
            lastWindowTitle = currentTitle;
            updateUI(message);
            fetchHistory(); // Refresh the side list
        }
    } else {
        addEventToLog(message);
    }
};

ws.onclose = () => {
    statusBadge.innerText = "OFFLINE";
    statusBadge.className = "status-offline";
};

function updateUI(event) {
    document.getElementById('active-app').innerText = event.data.app_type.toUpperCase();
    document.getElementById('active-window').innerText = event.data.window_title;
    addEventToLog(event);
}

function addEventToLog(event) {
    const entry = document.createElement('div');
    entry.className = `log-entry`;
    
    const time = new Date().toLocaleTimeString([], { hour12: false });
    const source = event.source ? event.source.toUpperCase() : "SYSTEM";

    entry.innerHTML = `
        <span class="log-time">${time}</span>
        <span class="log-source">[${source}]</span>
        <span class="log-msg">${event.event_type}: ${JSON.stringify(event.data)}</span>
    `;
    
    eventLog.prepend(entry);
    if (eventLog.children.length > 25) eventLog.removeChild(eventLog.lastChild);
}

// --- UPDATED: History Deduplication ---
async function fetchHistory() {
    try {
        const response = await fetch('http://localhost:8000/api/actions/history?limit=30');
        const data = await response.json();
        
        historyList.innerHTML = ''; 
        let lastSeenInHistory = "";

        data.actions.forEach(action => {
            const currentTitle = action.data.window_title;

            // Only append to the UI if it's different from the previous item in the log
            if (currentTitle !== lastSeenInHistory) {
                const item = document.createElement('div');
                item.className = 'history-item';
                item.innerHTML = `
                    <span class="h-time">${action.timestamp.split(' ')[1]}</span>
                    <span class="h-title">${currentTitle}</span>
                `;
                historyList.appendChild(item);
                lastSeenInHistory = currentTitle;
            }
        });
    } catch (err) {
        console.error("History fetch failed:", err);
    }
}

fetchHistory();
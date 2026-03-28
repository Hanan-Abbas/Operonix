import os
import json
from fastapi import APIRouter, HTTPException
from typing import List

router = APIRouter()

# The path to our actions log
LOG_FILE = "logs/actions.log"

@router.get("/actions/history")
async def get_action_history(limit: int = 50):
    """
    Reads the last N actions from the log file and returns them.
    """
    if not os.path.exists(LOG_FILE):
        return {"actions": []}

    actions = []
    try:
        with open(LOG_FILE, "r") as f:
            # Read all lines, reverse them to get newest first, and take the 'limit'
            lines = f.readlines()
            recent_lines = lines[-limit:]
            recent_lines.reverse()

            for line in recent_lines:
                if line.strip():
                    actions.append(json.loads(line))
        
        return {"actions": actions}
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read logs: {str(e)}")

@router.delete("/actions/clear")
async def clear_action_history():
    """Clears the history file."""
    try:
        if os.path.exists(LOG_FILE):
            os.remove(LOG_FILE)
        return {"status": "success", "message": "History cleared"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to clear logs: {str(e)}")
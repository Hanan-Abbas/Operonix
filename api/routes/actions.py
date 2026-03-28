import os
import json
from fastapi import APIRouter, HTTPException
from typing import List

router = APIRouter()

# The path to our actions log
LOG_FILE = "logs/actions.log"


"""
Test Graph Task Submission

Simple script to test LangGraph integration through the API.
"""
import requests
import json

# Test task submission
url = "http://localhost:8000/api/tasks/submit"
payload = {
    "user_input": "List files in current directory",
    "source": "api",
    "use_graph": True
}

print("Submitting task to LangGraph workflow...")
print(f"URL: {url}")
print(f"Payload: {json.dumps(payload, indent=2)}")
print()

try:
    response = requests.post(url, json=payload)
    print(f"Status Code: {response.status_code}")
    print(f"Response: {json.dumps(response.json(), indent=2)}")
except Exception as e:
    print(f"Error: {e}")

import asyncio
import base64
import io
import json
import logging
from core.config import settings
from automation.screen_reader import screen_reader
# Using the existing LLM client from the brain
from brain.llm_client import llm_client 


class VisionModel:
    """
    👁️ The Visual Brain.
    Used when text-based OCR (ScreenReader) fails to identify buttons like logos, 
    commit checkboxes, or UI elements with ambiguous labels.
    """

    def __init__(self):
        self.logger = logging.getLogger("VisionModel")

    async def find_element_visually(self, element_description: str) -> dict:
        """
        Takes a screenshot and asks the multi-modal LLM to locate a visual target 
        such as "the commit button in the desktop app" or "the search bar".
        """
        self.logger.info(f"Using Vision Model to find: '{element_description}'")

        # 1. Grab a fresh screenshot using your screen reader
        screenshot = await screen_reader.capture_screen()
        if not screenshot:
            return {"status": "error", "message": "Failed to capture screen for vision."}

        # 2. Convert image to Base64 so it can be sent to the model via JSON
        buffered = io.BytesIO()
        # Optimize image size slightly to speed up network transfer to local LLM
        screenshot.save(buffered, format="JPEG", quality=80)
        img_str = base64.b64encode(buffered.getvalue()).decode('utf-8')

        # 3. Create a strict prompt instructing the model to give us pure coordinates
        prompt = f"""
        Look at this screenshot. Find the element described as: "{element_description}".
        Return the exact center X and Y pixel coordinates where a mouse should click to activate it.
        
        You MUST return your answer in this exact JSON format with no other text:
        {{
            "x": <integer_x_coordinate>,
            "y": <integer_y_coordinate>,
            "confidence": <float_between_0_and_1>
        }}
        """

        try:
            # Send both the prompt and the image to the model
            # Note: This requires a multi-modal model like Ollama (with llava or bakllava) or Gemini
            response = await llm_client.generate_with_image(prompt, img_str, use_json=True)
            data = json.loads(response)
            
            # Basic validation of coordinates
            if "x" in data and "y" in data:
                self.logger.info(f"🎯 Vision Model located '{element_description}' at ({data['x']}, {data['y']})")
                return {
                    "status": "success",
                    "x": data["x"],
                    "y": data["y"],
                    "confidence": data.get("confidence", 0.5)
                }
                
            return {"status": "error", "message": "Model failed to return valid coordinates."}

        except Exception as e:
            self.logger.error(f"Vision analysis failed: {e}")
            return {"status": "error", "message": str(e)}


# Global instance
vision_model = VisionModel()
# capabilities/registry.py
import asyncio
import importlib
import pkgutil
import logging

logger = logging.getLogger("CapabilityRegistry")
logging.basicConfig(level=logging.INFO)

class CapabilityRegistry:
    """
    🧩 Centralized registry for all capabilities in the system.
    - Registers all ops (text, file, command, UI, web)
    - Provides validated, structured actions
    - Extensible and async-safe
    """

    def __init__(self):
        self.registry = {}  # intent_name -> async function
        self.validation_rules = []  # list of async or sync validation functions

    # -------------------------
    # Registration Methods
    # -------------------------
    def register(self, name: str, func):
        """Register a capability function."""
        if not asyncio.iscoroutinefunction(func):
            raise ValueError(f"Capability {name} must be an async function")
        self.registry[name] = func
        logger.info(f"✅ Registered capability: {name}")

    def get(self, name: str):
        """Retrieve a capability function by intent name."""
        return self.registry.get(name)

    # -------------------------
    # Validation Methods
    # -------------------------
    def add_validation_rule(self, rule_func):
        """Add a validation function (async or sync)."""
        self.validation_rules.append(rule_func)
        logger.info(f"✅ Added validation rule: {rule_func.__name__}")

    async def validate(self, action_data, args=None):
        """Run all validations, returns (is_valid, error_message)"""
        for rule in self.validation_rules:
            result, msg = await self._maybe_async(rule, action_data, args or {})
            if not result:
                logger.warning(f"❌ Validation failed: {rule.__name__} - {msg}")
                return False, msg
        return True, None

    async def _maybe_async(self, func, *args, **kwargs):
        """Run function, supports sync or async."""
        if asyncio.iscoroutinefunction(func):
            return await func(*args, **kwargs)
        return func(*args, **kwargs)

    # -------------------------
    # Unified Execution Interface
    # -------------------------
    async def execute(self, intent_name, context, args):
        """
        Unified interface to execute a capability:
        - Fetch capability
        - Run it
        - Validate output
        Returns (success: bool, action_data or error_message)
        """
        capability = self.get(intent_name)
        if capability is None:
            msg = f"No capability registered for intent: {intent_name}"
            logger.error(f"❌ {msg}")
            return False, msg

        try:
            action_data = await capability(context, args)
            logger.info(f"🎯 Executed capability: {intent_name}")
        except Exception as e:
            msg = f"Error executing capability {intent_name}: {str(e)}"
            logger.error(f"❌ {msg}")
            return False, msg

        # Run validations
        valid, error_msg = await self.validate(action_data, args)
        if not valid:
            msg = f"Validation failed for {intent_name}: {error_msg}"
            logger.error(f"❌ {msg}")
            return False, msg

        return True, action_data

    # -------------------------
    # Auto-Register All Ops
    # -------------------------
    def auto_register_ops(self, ops_package):
        """
        Automatically imports all *_ops.py modules in a given package and registers async functions.
        ops_package: Python package (like capabilities)
        """
        package_path = ops_package.__path__[0]

        for loader, module_name, is_pkg in pkgutil.iter_modules([package_path]):
            if module_name.endswith("_ops"):
                full_module_name = f"{ops_package.__name__}.{module_name}"
                module = importlib.import_module(full_module_name)

                # Register all async public functions in the module
                for attr_name in dir(module):
                    if attr_name.startswith("_"):  # skip private/internal
                        continue
                    attr = getattr(module, attr_name)
                    if asyncio.iscoroutinefunction(attr):
                        self.register(attr_name, attr)

# -------------------------
# Initialize global registry
# -------------------------
capability_registry = CapabilityRegistry()

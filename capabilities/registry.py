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
        self.validation_rules = []  # legacy global rules (avoid heavy use)
        self.intent_validation_rules = {}  # intent_name -> list of rule funcs

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

    # 🔗 NEW: Bridge for CapabilityMapper
    def get_all_names(self):
        """Returns a list of all registered capability names.
        
        Required by the vector CapabilityMapper to generate embeddings on boot.
        """
        return list(self.registry.keys())

    # -------------------------
    # Validation Methods
    # -------------------------
    def add_validation_rule(self, rule_func):
        """Add a global validation function (async or sync)."""
        self.validation_rules.append(rule_func)
        logger.info(f"✅ Added validation rule: {rule_func.__name__}")

    def add_intent_validation(self, intent_name: str, rule_func):
        """Attach a rule that runs only for a specific capability/intent name."""
        self.intent_validation_rules.setdefault(intent_name, []).append(rule_func)
        logger.info(f"✅ Validation for '{intent_name}': {rule_func.__name__}")

    def list_registered(self):
        return sorted(self.registry.keys())

    async def validate(self, intent_name, action_data, args=None):
        """Run intent-scoped rules, then global rules. Returns (is_valid, error_message)."""
        merged = {**(args or {}), **(action_data.get("args") or {})}
        for rule in self.intent_validation_rules.get(intent_name, []):
            result, msg = await self._maybe_async(rule, action_data, merged)
            if not result:
                logger.warning(f"❌ Validation failed: {rule.__name__} - {msg}")
                return False, msg
        for rule in self.validation_rules:
            result, msg = await self._maybe_async(rule, action_data, merged)
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
        - Fetch capability -> Run it -> Validate output
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

        # Run validations (intent-scoped + global)
        valid, error_msg = await self.validate(intent_name, action_data, args)
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
                    if attr_name.startswith("_"):
                        continue
                    # 🔄 UPGRADE: Added 'normalize_' and 'parse_' to protected words 
                    # so helper functions don't get exposed as user intents!
                    if (attr_name.startswith("validate_") or 
                        attr_name.startswith("normalize_") or
                        attr_name.startswith("parse_") or
                        attr_name.endswith("_check")):
                        continue
                        
                    attr = getattr(module, attr_name)
                    if asyncio.iscoroutinefunction(attr):
                        self.register(attr_name, attr)

# -------------------------
# Initialize global registry
# -------------------------
capability_registry = CapabilityRegistry()
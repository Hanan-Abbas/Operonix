"""
Resource Manager — Operonix Graph
──────────────────────────────────

Resource manager for physical desktop resource ownership tracking.
Per migration plan Phase 8: Cancellation, Timeout & Resource Control

Resource rule: "logical workflow concurrency ≠ physical desktop concurrency"
Graph instances may coexist, but physical resources such as keyboard, mouse,
active window, and focus may require serialization.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional
from datetime import datetime, timedelta

from migration.domain_contracts import ResourceOwnership, ResourceType

logger = logging.getLogger("Graph.ResourceManager")


class ResourceManager:
    """Manager for physical desktop resource ownership tracking.
    
    Per migration plan Phase 8: Resource rule
    "logical workflow concurrency ≠ physical desktop concurrency"
    """
    
    def __init__(self):
        """Initialize resource manager."""
        self.active_ownerships: Dict[str, ResourceOwnership] = {}
        self.resource_locks: Dict[ResourceType, str] = {}  # resource_type -> task_id
        
        logger.info("ResourceManager initialized")
    
    def acquire_resource(self, task_id: str, resource_type: ResourceType, resource_identifier: Optional[str] = None, expires_in_seconds: Optional[int] = None) -> Optional[ResourceOwnership]:
        """Acquire ownership of a physical resource.
        
        Args:
            task_id: Task identifier
            resource_type: Type of resource to acquire
            resource_identifier: Optional resource identifier (e.g., window title, file path)
            expires_in_seconds: Optional expiration time in seconds
            
        Returns:
            ResourceOwnership if acquired, None if resource is already locked
        """
        # Check if resource is already locked by another task
        if resource_type in self.resource_locks:
            current_owner = self.resource_locks[resource_type]
            if current_owner != task_id:
                logger.warning(f"Resource {resource_type.value} already locked by task {current_owner}")
                return None
        
        # Create ownership record
        ownership = ResourceOwnership(
            task_id=task_id,
            resource_type=resource_type,
            resource_identifier=resource_identifier
        )
        
        if expires_in_seconds:
            ownership.expires_at = datetime.utcnow() + timedelta(seconds=expires_in_seconds)
        
        # Store ownership
        self.active_ownerships[ownership.ownership_id] = ownership
        self.resource_locks[resource_type] = task_id
        
        logger.info(f"Resource acquired: {resource_type.value} by task {task_id} (ownership_id: {ownership.ownership_id})")
        
        return ownership
    
    def release_resource(self, ownership_id: str) -> bool:
        """Release ownership of a resource.
        
        Args:
            ownership_id: Ownership identifier to release
            
        Returns:
            True if released, False if not found
        """
        if ownership_id not in self.active_ownerships:
            logger.warning(f"Ownership not found: {ownership_id}")
            return False
        
        ownership = self.active_ownerships[ownership_id]
        
        # Remove from active ownerships
        del self.active_ownerships[ownership_id]
        
        # Remove resource lock if this was the only owner
        if self.resource_locks.get(ownership.resource_type) == ownership.task_id:
            # Check if there are other ownerships for this resource type by this task
            other_ownerships = [o for o in self.active_ownerships.values() 
                               if o.resource_type == ownership.resource_type and o.task_id == ownership.task_id]
            if not other_ownerships:
                del self.resource_locks[ownership.resource_type]
        
        logger.info(f"Resource released: {ownership.resource_type.value} (ownership_id: {ownership_id})")
        
        return True
    
    def release_all_resources_for_task(self, task_id: str) -> int:
        """Release all resources owned by a task.
        
        Args:
            task_id: Task identifier
            
        Returns:
            Number of resources released
        """
        released_count = 0
        
        # Find all ownerships for this task
        ownerships_to_release = [
            ownership_id for ownership_id, ownership in self.active_ownerships.items()
            if ownership.task_id == task_id
        ]
        
        for ownership_id in ownerships_to_release:
            if self.release_resource(ownership_id):
                released_count += 1
        
        logger.info(f"Released {released_count} resources for task {task_id}")
        
        return released_count
    
    def check_resource_availability(self, resource_type: ResourceType, task_id: str) -> bool:
        """Check if a resource is available for a task.
        
        Args:
            resource_type: Type of resource to check
            task_id: Task identifier
            
        Returns:
            True if available, False if locked by another task
        """
        if resource_type not in self.resource_locks:
            return True
        
        return self.resource_locks[resource_type] == task_id
    
    def get_active_ownerships(self, task_id: Optional[str] = None) -> List[ResourceOwnership]:
        """Get active resource ownerships.
        
        Args:
            task_id: Optional task ID to filter by
            
        Returns:
            List of active ResourceOwnership
        """
        if task_id:
            return [ownership for ownership in self.active_ownerships.values() if ownership.task_id == task_id]
        return list(self.active_ownerships.values())
    
    def cleanup_expired_ownerships(self) -> int:
        """Clean up expired resource ownerships.
        
        Returns:
            Number of ownerships cleaned up
        """
        now = datetime.utcnow()
        expired_count = 0
        
        ownerships_to_expire = [
            ownership_id for ownership_id, ownership in self.active_ownerships.items()
            if ownership.expires_at and ownership.expires_at <= now
        ]
        
        for ownership_id in ownerships_to_expire:
            if self.release_resource(ownership_id):
                expired_count += 1
        
        if expired_count > 0:
            logger.info(f"Cleaned up {expired_count} expired resource ownerships")
        
        return expired_count
    
    def get_resource_locks(self) -> Dict[ResourceType, str]:
        """Get current resource locks.
        
        Returns:
            Dict mapping resource types to task IDs
        """
        return self.resource_locks.copy()


# Global resource manager instance
_resource_manager: Optional[ResourceManager] = None


def get_resource_manager() -> ResourceManager:
    """Get the global resource manager instance.
    
    Returns:
        ResourceManager instance
    """
    global _resource_manager
    
    if _resource_manager is None:
        _resource_manager = ResourceManager()
    
    return _resource_manager

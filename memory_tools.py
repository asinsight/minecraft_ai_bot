"""
Spatial Memory Tools — LangChain tools for saving/finding locations.
"""

from langchain.tools import tool
from spatial_memory import SpatialMemory

memory = SpatialMemory()


@tool
def save_location(name: str, category: str, x: float, y: float, z: float, description: str = "") -> str:
    """Save a location to remember later.
    Args:
        name: Unique name (e.g., 'iron_cave', 'main_shelter')
        category: shelter, crafting, storage, resource, poi, custom
        x, y, z: Coordinates
        description: What's here
    """
    return memory.save_location(name, category, x, y, z, description)


@tool
def delete_location(name: str) -> str:
    """Delete a saved location."""
    return memory.delete_location(name)


@tool
def find_nearest_location(category: str = "") -> str:
    """Find nearest saved location, optionally by category."""
    return memory.find_nearest(category)


@tool
def list_locations(category: str = "") -> str:
    """List all saved locations."""
    return memory.list_locations(category)


@tool
def recall_location(name: str) -> str:
    """Look up a specific saved location by name."""
    return memory.get_location(name)


MEMORY_TOOLS = [save_location, delete_location, find_nearest_location, list_locations, recall_location]

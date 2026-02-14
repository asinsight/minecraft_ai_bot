"""
Spatial Memory Tools — LangChain tools for saving/finding important locations.
"""

from langchain.tools import tool
from spatial_memory import SpatialMemory

# Shared instance — imported by agent.py
memory = SpatialMemory()


@tool
def save_location(name: str, category: str, x: float, y: float, z: float, description: str = "") -> str:
    """Save an important location to remember later. Use this whenever you find
    or place something worth remembering.

    Args:
        name: Unique name for this place (e.g., 'main_shelter', 'iron_cave', 'chest_tools')
        category: One of: 'shelter', 'crafting', 'storage', 'resource', 'poi', 'custom'
        x: X coordinate
        y: Y coordinate
        z: Z coordinate
        description: What's here (e.g., 'Oak shelter with door facing north')
    """
    return memory.save_location(name, category, x, y, z, description)


@tool
def delete_location(name: str) -> str:
    """Delete a saved location that's no longer useful.

    Args:
        name: Name of the location to delete
    """
    return memory.delete_location(name)


@tool
def find_nearest_location(category: str = "") -> str:
    """Find the nearest saved location. Optionally filter by category.

    Args:
        category: Filter by category (shelter, crafting, storage, resource, poi). Leave empty for any.
    """
    return memory.find_nearest(category)


@tool
def list_locations(category: str = "") -> str:
    """List all saved locations. Optionally filter by category.

    Args:
        category: Filter by category. Leave empty for all locations.
    """
    return memory.list_locations(category)


@tool
def recall_location(name: str) -> str:
    """Look up a specific saved location by name.

    Args:
        name: Name of the saved location (e.g., 'main_shelter', 'crafting_table')
    """
    return memory.get_location(name)


MEMORY_TOOLS = [
    save_location,
    delete_location,
    find_nearest_location,
    list_locations,
    recall_location,
]
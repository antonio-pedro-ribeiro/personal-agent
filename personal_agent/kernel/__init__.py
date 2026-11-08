"""Microkernel: config, plugin registry, run loop and shared services.

The kernel knows nothing about events, news, calendars or notes, and must never import
from ``personal_agent.plugins``.
"""

"""
API Call Counter Service

Tracks AWS API calls (Lambda, Textract, Bedrock) for rate limit monitoring.
Uses thread-safe in-memory counter.
"""

import threading
from typing import Dict
from datetime import datetime

class APICounter:
    """
    Thread-safe counter for tracking AWS API calls.
    """
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(APICounter, cls).__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._counts: Dict[str, int] = {
            'lambda': 0,
            'textract': 0,
            'bedrock': 0,
            'total': 0
        }
        self._lock = threading.Lock()
        self._start_time = datetime.utcnow()
        self._initialized = True
    
    def increment(self, service: str):
        """
        Increment counter for a specific AWS service.
        
        Args:
            service: Service name ('lambda', 'textract', 'bedrock')
        """
        with self._lock:
            if service in self._counts:
                self._counts[service] += 1
                self._counts['total'] += 1
    
    def get_count(self, service: str = 'total') -> int:
        """
        Get count for a specific service or total.
        
        Args:
            service: Service name ('lambda', 'textract', 'bedrock', 'total')
            
        Returns:
            Count for the service
        """
        with self._lock:
            return self._counts.get(service, 0)
    
    def get_all_counts(self) -> Dict[str, int]:
        """
        Get all service counts.
        
        Returns:
            Dictionary with all counts
        """
        with self._lock:
            return self._counts.copy()
    
    def reset(self):
        """Reset all counters."""
        with self._lock:
            self._counts = {
                'lambda': 0,
                'textract': 0,
                'bedrock': 0,
                'total': 0
            }
            self._start_time = datetime.utcnow()
    
    def get_stats(self) -> Dict:
        """
        Get statistics including counts and uptime.
        
        Returns:
            Dictionary with counts and uptime
        """
        with self._lock:
            uptime = (datetime.utcnow() - self._start_time).total_seconds()
            return {
                'counts': self._counts.copy(),
                'uptime_seconds': int(uptime),
                'start_time': self._start_time.isoformat()
            }

# Global instance
api_counter = APICounter()

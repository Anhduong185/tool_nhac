from config import RULES
from datetime import datetime

class FilterEngine:
    @staticmethod
    def is_valid(audio_data):
        """
        audio_data structure:
        {
            'duration': int,
            'usage_count': int,
            'year': int,
            'is_ai': bool,
            'is_game': bool,
            'is_voice_only': bool,
            'is_copyrighted': bool,
            'recent_usage': int (in last 7 days),
            'source_type': str ('ai', 'game', 'podcast', 'stickman', 'brand', 'movie')
        }
        """
        # 1. Basic checks
        if audio_data['duration'] > RULES['max_duration']:
            return False, "Duration too long"
            
        if audio_data['is_copyrighted']:
            return False, "Copyright detected"
            
        if not audio_data['is_voice_only']:
            return False, "No clear voice"

        # 2. Year-based rules
        year = audio_data.get('year', 2024)
        usage = audio_data['usage_count']
        recent_usage = audio_data.get('recent_usage', 0)

        if year == 2023:
            if usage < RULES['years'][2023]['min_usage']:
                # Accept if recent reuse is high
                if recent_usage < 100: # Threshold for recent reuse
                    return False, "2023 audio, usage too low"
        
        elif year == 2024:
            if usage < RULES['years'][2024]['min_usage']:
                return False, "2024 audio, usage too low"
                
        elif year >= 2025:
            if usage < RULES['years'][2025]['min_usage']:
                return False, "New audio, usage too low"

        # 3. Source-specific rules
        source = audio_data.get('source_type')
        
        if source == 'ai':
            if usage < RULES['source_ai']['min_usage'] and recent_usage < RULES['source_ai']['recent_usage']:
                return False, "AI source, usage too low"
        
        elif source == 'game':
            if year == 2026 and usage < RULES['source_game']['2026_min_usage']:
                return False, "Game source 2026, usage too low"
            # 2023 game needs recent reuse (already handled by year rule generally, but can be specific)

        if source in ['movie', 'tv show', 'brand']:
            return False, f"Source '{source}' is excluded"

        return True, "Passed"

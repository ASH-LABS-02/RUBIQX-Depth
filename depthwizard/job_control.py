"""Cooperative processing controls; cancellation never publishes a completed job."""
class JobCancelled(RuntimeError):
    pass


def stage_for(message):
    for prefix, stage, percent in (
        ("reading", "Read imagery", 5), ("fetching", "Acquire terrain", 10),
        ("relative height", "Depth inference", 20), ("scale calibration", "Calibrate", 55),
        ("experimental overhead", "Classify", 65), ("extracting", "Extract buildings", 70),
        ("validating", "Validate", 85), ("export", "Export", 95)):
        if str(message).startswith(prefix):
            return stage, percent
    return None

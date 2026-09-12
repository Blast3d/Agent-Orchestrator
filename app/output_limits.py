"""Hard caps for worker pipe memory, queues, and public previews.

These are process-local resource bounds. They never extend an execution
deadline and they do not infer provider completion. Values are module
attributes so tests can patch them without changing public APIs.
"""


class OutputLimitExceeded(Exception):
    """A local pipe, queue, or payload bound was exceeded."""


# Bytes read from each child pipe per syscall.
READ_CHUNK = 16 * 1024

# In-flight decoded pipe events waiting for the parent loop.
QUEUE_MAX = 64

# One assembled stream-json line or parsed terminal payload (UTF-8 bytes).
EVENT_MAX = 1024 * 1024
TERMINAL_MAX = 1024 * 1024

# Retained non-stream stdout and private stderr (UTF-8 bytes).
BUFFERED_STDOUT_MAX = 4 * 1024 * 1024
STDERR_MAX = 256 * 1024

# Public partial-answer preview (UTF-8 bytes). Observed answer text may continue
# past this; the hard event/terminal caps still apply.
PARTIAL_PREVIEW_MAX = 128 * 1024

from short_drama.agent.core.fence import owns_execution as owns_execution
from short_drama.agent.runtime.operations import claim_operation, complete_operation
from short_drama.agent.tools.contracts import MEMORY_READ_LIMIT as MEMORY_READ_LIMIT
from short_drama.agent.tools.contracts import TOOL_SCHEMAS as TOOL_SCHEMAS
from short_drama.agent.tools.contracts import DescribeTarget as DescribeTarget
from short_drama.agent.tools.contracts import MemoryCatalog as MemoryCatalog
from short_drama.agent.tools.contracts import ReadHistory as ReadHistory
from short_drama.agent.tools.contracts import ReadMemory as ReadMemory
from short_drama.agent.tools.contracts import ReadReceipt as ReadReceipt
from short_drama.agent.tools.contracts import ReadSelectedSubjects as ReadSelectedSubjects
from short_drama.agent.tools.contracts import SearchMemory as SearchMemory
from short_drama.agent.tools.contracts import tool_manifest as tool_manifest


def execute_tools(factory, settings, run_id, *, fence=None, watch=None):
    while True:
        if watch is not None and watch.suspect:
            return
        operation_id = claim_operation(factory, settings, run_id, fence=fence)
        if operation_id is None:
            return
        complete_operation(factory, settings, run_id, operation_id, fence=fence)
        if watch is not None and watch.suspect:
            # A lost lease must not keep starting new operations in this delivery.
            return

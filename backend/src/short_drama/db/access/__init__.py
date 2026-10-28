"""ORM 权限守卫的唯一注册入口；应用和测试均由 db.session 加载。"""

from short_drama.db.access import queries as queries
from short_drama.db.access import writes as writes

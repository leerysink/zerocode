-- 平台网关动态路由（OpenResty / lua）。
-- access 阶段：从 Host 取一级子域 slug → 查 routes.json（内存缓存）→ 得 prod_port
-- → 设 ngx.var.zerocode_upstream = host:port 交回 nginx proxy_pass；查不到给 404 提示页。
--
-- 域名后缀不写死在本文件里：由环境变量 GATEWAY_DOMAIN 给出（compose 从 .env 的
-- ZEROCODE_GATEWAY_DOMAIN 透传），与平台侧 app/deploy/nics.py 的 GATEWAY_DOMAIN 同源。
-- 两边对不上的症状是「控制台显示的地址看着正常，点进去全 404」。

local cjson = require "cjson.safe"

local M = {}

local CACHE_TTL = 5                       -- 秒；到点重读文件，内容变了即刷新（等价 mtime 判定，免 lfs 依赖）
local DEFAULT_HOST = "host.docker.internal"
local DEFAULT_ROUTES = "/etc/nginx/routes/routes.json"
local PAGE_404 = "/etc/nginx/404.html"
local DEFAULT_DOMAIN = "zerocode.com"   -- 本机自签演示的默认值；服务器上由 GATEWAY_DOMAIN 覆盖

-- 纯函数：把域名归一成模式可用的形态。
-- 容忍两种常见写法：nginx server_name 习惯的前导点（.example.com）、DNS 的结尾根点
-- （example.com.）。未设 / 空串一律退回默认值——空域名会拼出「匹配一切一级子域」的模式，
-- 那比匹配不上糟得多。
function M.normalize_domain(raw)
    if not raw then return DEFAULT_DOMAIN end
    local d = tostring(raw):lower()
    d = d:match("^%.+(.+)") or d          -- 去前导点
    d = d:match("(.+)%.$") or d           -- 去结尾根点
    if d == "" then return DEFAULT_DOMAIN end
    return d
end

local function gateway_domain()
    -- 陷阱：nginx 主上下文必须声明 `env GATEWAY_DOMAIN;`。nginx 默认清空继承来的环境，
    -- 没声明时 os.getenv 静默返回 nil → 退回默认域名 → 换域名后全站 404，且不报错不告警。
    return M.normalize_domain(os.getenv("GATEWAY_DOMAIN"))
end

-- 纯函数：把域名转义成 Lua 模式字面量。域名里的 `.` 在模式中是「任意字符」，不转义会让
-- `appXexample.com` 也匹配上；`-` 更是量词（惰性重复），带连字符的域名不转义会直接匹配不上。
-- 替换串用 %0（整个匹配）而不是 %1：前者是手册明确定义的，后者在「模式里没有显式捕获」时
-- 靠的是实现约定，不值得依赖。
local MAGIC = "[%^%$%(%)%*%+%-%[%]%?%.]"
local function escape_pattern(s)
    return (s:gsub(MAGIC, "%%%0"))
end

-- 纯函数：从 host 取一级子域 slug；顶域/多级/非法一律返回 nil。
-- domain 显式传入时不读环境变量——这是为了让本函数能在没有 ngx、没法改进程环境的
-- 普通 Lua 跑器里被直接测到。
--
-- 通配证书只覆盖一级，所以刻意不接受 `a.b.<域名>`（平台侧 slugs.SLUG_RE 同样禁点号）。
-- 每请求重算一次转义而不做缓存：成本是几个短字符串操作，换来的是无模块级可变状态。
function M.slug_of(host, domain)
    if not host then return nil end
    local d = M.normalize_domain(domain or os.getenv("GATEWAY_DOMAIN"))
    -- 先去端口再去 DNS 结尾根点，顺序不能反：`test1.example.com.:8443` 两者都有。
    -- 根点是合法写法，nginx 的 $host 不会剥掉它；不剥就会 404，而 404 页把这事说成
    -- 「应用不存在」——排查方向直接错。
    local no_port = (host:gsub(":.*$", ""))
    local h = (no_port:gsub("%.$", "")):lower()
    return h:match("^([a-z0-9]+)%." .. escape_pattern(d) .. "$")
end

-- 纯函数：当前网关域名。404 页文案要用它——域名不能再写死在静态页里，
-- 否则演示当场会看到「域名 xxx.zerocode.com 对应的应用未运行」这种谎话。
function M.domain()
    return gateway_domain()
end

-- 纯函数：路由表里查 slug 对应端口；无则 nil
function M.lookup(routes, slug)
    if not routes or not slug then return nil end
    return routes[slug]
end

local function upstream_host()
    return os.getenv("UPSTREAM_HOST") or DEFAULT_HOST
end

local function routes_path()
    return os.getenv("ROUTES_PATH") or DEFAULT_ROUTES
end

-- 读文件（读不到返回 nil）
local function read_file(path)
    local f = io.open(path, "r")
    if not f then return nil end
    local s = f:read("*a")
    f:close()
    return s
end

-- 带 TTL 的路由表加载：未到刷新点且已有缓存 → 用缓存；否则重读文件，内容没变只推迟下次重读
local function load_routes()
    local shared = ngx.shared.routes_cache
    local now = ngx.now()
    local cached, last = shared:get("routes_json"), shared:get("last_load")

    if cached and last and (now - last) < CACHE_TTL then
        return cjson.decode(cached) or {}
    end

    local text = read_file(routes_path())
    shared:set("last_load", now)
    if not text then
        return cached and (cjson.decode(cached) or {}) or {}   -- 文件缺失：沿用旧表
    end
    if text == cached then
        return cjson.decode(text) or {}                          -- 内容未变
    end
    shared:set("routes_json", text)                             -- 内容变了：换表
    return cjson.decode(text) or {}
end

function M.render_404(slug)
    ngx.status = ngx.HTTP_NOT_FOUND
    ngx.header["Content-Type"] = "text/html; charset=utf-8"
    local body = read_file(PAGE_404)
        or "<!doctype html><meta charset=utf-8><h1>404</h1><p>应用未运行或不存在</p>"
    -- 替换值一律用函数形式而不是字符串：gsub 的字符串替换会把 `%` 当捕捉引用，
    -- 而 ngx.escape_uri 的输出恰好全是 %XX，一旦 slug 含需转义的字符就会报
    -- 「invalid capture index」。当前 slug 只能是 [a-z0-9]+ 所以不可达，但函数形式不依赖这个前提。
    body = body:gsub("{{SLUG}}", function() return ngx.escape_uri(slug or "") end)
    body = body:gsub("{{DOMAIN}}", function() return gateway_domain() end)
    ngx.print(body)
end

function M.dispatch()
    local slug = M.slug_of(ngx.var.host)
    if not slug then
        return ngx.exit(ngx.HTTP_NOT_FOUND)
    end
    local port = M.lookup(load_routes(), slug)
    if not port then
        M.render_404(slug)
        return ngx.exit(ngx.HTTP_NOT_FOUND)
    end
    ngx.var.zerocode_upstream = upstream_host() .. ":" .. tostring(port)
end

return M

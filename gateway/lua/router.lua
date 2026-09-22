-- 平台网关动态路由（OpenResty / lua）。
-- access 阶段：从 Host 取一级子域 slug → 查 routes.json（内存缓存）→ 得 prod_port
-- → 设 ngx.var.zerocode_upstream = host:port 交回 nginx proxy_pass；查不到给 404 提示页。

local cjson = require "cjson.safe"

local M = {}

local CACHE_TTL = 5                       -- 秒；到点重读文件，内容变了即刷新（等价 mtime 判定，免 lfs 依赖）
local DEFAULT_HOST = "host.docker.internal"
local DEFAULT_ROUTES = "/etc/nginx/routes/routes.json"
local PAGE_404 = "/etc/nginx/404.html"

-- 纯函数：从 host 取一级子域 slug（*.zerocode.com）；顶域/多级/非法一律返回 nil
function M.slug_of(host)
    if not host then return nil end
    local h = (host:gsub(":.*$", "")):lower()
    return h:match("^([a-z0-9]+)%.zerocode%.com$")
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
    body = (body:gsub("{{SLUG}}", ngx.escape_uri(slug or "")))
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

-- Minimal mock of the WoW client API, enough to load and drive tools/addons/* under LuaJIT
-- (Lua 5.1 semantics, like the game). Not a full emulation: only what the addons touch, with
-- the argument and return shapes of the 1.15/1.60 Classic client.
--
-- Usage from a scenario:
--   dofile("tests/wowmock.lua")
--   wow.load_savedvariables(path)            -- what the client does before ADDON_LOADED
--   wow.load_addon("tools/addons/X/X.lua", "X")
--   wow.fire("PLAYER_LOGIN")
--   ... set wow.state, fire events ...
--   wow.write_savedvariables(path, {"XDB"})  -- what the client does on logout
--   wow.report()                              -- undefined globals read, prints, errors

wow = { state = {}, frames = {}, printed = {}, undefined = {}, now = 1792000000, clock = 1000.0 }
local S = wow.state

-- ── strict globals: record reads of names the mock does not define ──────────
local defined = {}
setmetatable(_G, {
    __index = function(_, k)
        if not defined[k] then wow.undefined[k] = true end
        return nil
    end,
    __newindex = function(t, k, v)
        defined[k] = true
        rawset(t, k, v)
    end,
})
for k in pairs(_G) do defined[k] = true end
local function def(name, value) defined[name] = true; rawset(_G, name, value) end

-- ── basics ─────────────────────────────────────────────────────────────────
def("print", function(...)
    local t = {}
    for i = 1, select("#", ...) do t[i] = tostring((select(i, ...))) end
    local line = table.concat(t, " ")
    wow.printed[#wow.printed + 1] = line
end)
def("time", function() return wow.now end)
def("GetTime", function() return wow.clock end)
def("GetLocale", function() return "enUS" end)
def("GetBuildInfo", function() return S.version or "1.60.1", S.build or "70374", "Oct 13 2026", S.interface or 16001 end)
def("UnitFactionGroup", function() return "Alliance", "Alliance" end)
def("strsplit", function(sep, s)
    local out, start = {}, 1
    while true do
        local i = string.find(s, sep, start, true)
        if not i then out[#out + 1] = string.sub(s, start); break end
        out[#out + 1] = string.sub(s, start, i - 1)
        start = i + 1
    end
    return unpack(out)
end)
def("hooksecurefunc", function(a, b, c)
    local tbl, name, hook = _G, a, b
    if type(a) == "table" then tbl, name, hook = a, b, c end
    local orig = tbl[name]
    assert(type(orig) == "function", "hooksecurefunc on a non-function " .. tostring(name))
    tbl[name] = function(...)
        local r = { orig(...) }
        hook(...)
        return unpack(r)
    end
end)
def("SlashCmdList", {})
def("Enum", { LootSlotType = { None = 0, Item = 1, Money = 2, Currency = 3 } })
def("LOOT_SLOT_MONEY", 2)

-- ── frames and events ────────────────────────────────────────────────────────
local KNOWN_EVENTS = {}
for _, e in ipairs({ "PLAYER_LOGIN", "ADDON_LOADED", "LOOT_OPENED", "MERCHANT_SHOW", "QUEST_DETAIL",
    "QUEST_PROGRESS", "QUEST_COMPLETE", "QUEST_ACCEPTED", "QUEST_TURNED_IN", "TRAINER_SHOW",
    "NAME_PLATE_UNIT_ADDED", "PLAYER_TARGET_CHANGED", "UPDATE_MOUSEOVER_UNIT", "UNIT_SPELLCAST_SUCCEEDED" }) do
    KNOWN_EVENTS[e] = true
end
wow.known_events = KNOWN_EVENTS

def("CreateFrame", function()
    local f = { events = {}, scripts = {} }
    function f:RegisterEvent(e)
        if not KNOWN_EVENTS[e] then error("Attempt to register unknown event \"" .. e .. "\"", 2) end
        self.events[e] = true
    end
    function f:UnregisterEvent(e) self.events[e] = nil end
    function f:SetScript(name, fn) self.scripts[name] = fn end
    wow.frames[#wow.frames + 1] = f
    return f
end)

function wow.fire(event, ...)
    for _, f in ipairs(wow.frames) do
        if f.events[event] and f.scripts.OnEvent then
            local ok, err = pcall(f.scripts.OnEvent, f, event, ...)
            if not ok then error("handler for " .. event .. " raised: " .. tostring(err)) end
        end
    end
end

function wow.advance(seconds)
    wow.now = wow.now + seconds
    wow.clock = wow.clock + seconds
end

-- ── units and GUIDs ──────────────────────────────────────────────────────────
S.units = {}   -- token -> { guid =, level = }
function wow.creature_guid(npc, spawn) return ("Creature-0-4410-0-1-%d-%s"):format(npc, spawn) end
function wow.object_guid(obj, spawn) return ("GameObject-0-4410-0-1-%d-%s"):format(obj, spawn) end
def("UnitGUID", function(unit) local u = S.units[unit]; return u and u.guid end)
def("UnitLevel", function(unit)
    if unit == "player" then return S.player_level or 20 end
    local u = S.units[unit]; return u and u.level
end)

-- ── position ─────────────────────────────────────────────────────────────────
S.pos = { map = 0, ui = 1437, x = 0.5321, y = 0.4010 }
def("GetInstanceInfo", function() return "Eastern Kingdoms", "none", 0, "", 5, 0, false, S.pos.map end)
def("C_Map", {
    GetBestMapForUnit = function(unit) return S.pos.ui end,
    GetPlayerMapPosition = function(ui, unit)
        if S.pos.x == nil then return nil end
        return { GetXY = function() return S.pos.x, S.pos.y end }
    end,
})

-- ── items and bags ───────────────────────────────────────────────────────────
local function item_link(id) return ("|cffffffff|Hitem:%d::::::::20:::::::|h[Item %d]|h|r"):format(id, id) end
wow.item_link = item_link
S.bags = {}   -- [bag][slot] = itemID
def("C_Container", {
    GetContainerItemID = function(bag, slot) return S.bags[bag] and S.bags[bag][slot] end,
    UseContainerItem = function(bag, slot) end,
})

-- ── loot ─────────────────────────────────────────────────────────────────────
-- S.loot = { fishing = bool, slots = { {item = id | nil, money = copper | nil, sources = {guid, qty, guid, qty}} } }
def("GetNumLootItems", function() return #(S.loot and S.loot.slots or {}) end)
def("GetLootSlotType", function(slot) return S.loot.slots[slot].money and 2 or 1 end)
def("GetLootSlotLink", function(slot) local s = S.loot.slots[slot]; return s.item and item_link(s.item) end)
def("GetLootSourceInfo", function(slot) return unpack(S.loot.slots[slot].sources) end)
def("IsFishingLoot", function() return S.loot and S.loot.fishing or false end)

-- ── merchant ─────────────────────────────────────────────────────────────────
-- S.merchant = { {id=, price=, stack=, available=-1, costs={{id, n}}} }
def("GetMerchantNumItems", function() return #S.merchant end)
def("GetMerchantItemID", function(i) return S.merchant[i].id end)
def("GetMerchantItemInfo", function(i)
    local m = S.merchant[i]
    return "Item " .. m.id, 133784, m.price, m.stack or 1, m.available or -1, true, true, m.costs ~= nil
end)
def("GetMerchantItemCostInfo", function(i) return S.merchant[i].costs and #S.merchant[i].costs or 0 end)
def("GetMerchantItemCostItem", function(i, k)
    local c = S.merchant[i].costs[k]
    return 133784, c[2], item_link(c[1]), nil
end)

-- ── trainer ──────────────────────────────────────────────────────────────────
-- S.trainer = { {name=, kind="available"|"header", cost=, creates=itemID|nil} }
def("GetNumTrainerServices", function() return #S.trainer end)
def("GetTrainerServiceInfo", function(i) local t = S.trainer[i]; return t.name, "", t.kind or "available" end)
def("GetTrainerServiceCost", function(i) return S.trainer[i].cost or 0 end)
def("GetTrainerServiceItemLink", function(i) local t = S.trainer[i]; return t.creates and item_link(t.creates) end)

-- ── addons and documentation ─────────────────────────────────────────────────
S.addons = {}   -- list of {name=, loaded=}
local TOC_VERSION = {}
for line in io.lines("tools/addons/OpenDataCollector/OpenDataCollector.toc") do
    local v = line:match("^## Version:%s*(%S+)")
    if v then TOC_VERSION.OpenDataCollector = v end
end
def("C_AddOns", {
    GetAddOnMetadata = function(name, field) return field == "Version" and TOC_VERSION[name] or nil end,
    GetNumAddOns = function() return #S.addons end,
    GetAddOnInfo = function(i) return S.addons[i].name end,
    IsAddOnLoaded = function(i)
        if type(i) == "string" then
            for _, a in ipairs(S.addons) do if a.name == i then return a.loaded end end
            return false
        end
        return S.addons[i].loaded
    end,
    LoadAddOn = function(name)
        if name == "Blizzard_APIDocumentationGenerated" and S.docs then
            def("APIDocumentation", { systems = S.docs })
            return true
        end
        return name == "Blizzard_APIDocumentation"
    end,
})

-- ── SavedVariables in and out ────────────────────────────────────────────────
function wow.load_savedvariables(path)
    local f = io.open(path, "r")
    if not f then return end
    f:close()
    local chunk = assert(loadfile(path))
    chunk()
end

-- Written like the client does: ["key"] = value, arrays with -- [n] comments, strings via %q.
local function write_value(out, v, indent)
    local t = type(v)
    if t == "string" then
        out[#out + 1] = string.format("%q", v)
    elseif t == "number" or t == "boolean" then
        out[#out + 1] = tostring(v)
    elseif t == "table" then
        out[#out + 1] = "{\n"
        local n = #v
        for i = 1, n do
            out[#out + 1] = indent .. "\t"
            write_value(out, v[i], indent .. "\t")
            out[#out + 1] = (", -- [%d]\n"):format(i)
        end
        local keys = {}
        for k in pairs(v) do
            if not (type(k) == "number" and k >= 1 and k <= n and k % 1 == 0) then keys[#keys + 1] = k end
        end
        table.sort(keys, function(a, b) return tostring(a) < tostring(b) end)
        for _, k in ipairs(keys) do
            out[#out + 1] = indent .. "\t[" .. (type(k) == "string" and string.format("%q", k) or tostring(k)) .. "] = "
            write_value(out, v[k], indent .. "\t")
            out[#out + 1] = ",\n"
        end
        out[#out + 1] = indent .. "}"
    else
        out[#out + 1] = "nil"
    end
end

function wow.write_savedvariables(path, names)
    local out = { "\n" }
    for _, name in ipairs(names) do
        local v = rawget(_G, name)
        if v ~= nil then
            out[#out + 1] = name .. " = "
            write_value(out, v, "")
            out[#out + 1] = "\n"
        end
    end
    local f = assert(io.open(path, "w"))
    f:write(table.concat(out))
    f:close()
end

function wow.load_addon(path, name)
    local chunk = assert(loadfile(path))
    chunk(name, {})
end

function wow.report(allowed_undefined)
    local bad = {}
    for k in pairs(wow.undefined) do
        if not (allowed_undefined and allowed_undefined[k]) then bad[#bad + 1] = k end
    end
    table.sort(bad)
    for _, line in ipairs(wow.printed) do io.stderr:write("chat: ", line, "\n") end
    if #bad > 0 then
        error("addon read undefined globals: " .. table.concat(bad, ", "))
    end
    for _, line in ipairs(wow.printed) do
        if line:find("failed") then error("addon reported a failure: " .. line) end
    end
end

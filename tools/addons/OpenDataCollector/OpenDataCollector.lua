-- OpenDataCollector: record in-game observations for the opendata world layer.
--
-- One batch per login session in OpenDataCollectorDB.batches. Each event is one string,
-- fields separated by tabs, so SavedVariables stay small. tools/uploader.py documents the
-- grammar and turns batches into JSON for the reconciler. Only Blizzard IDs, counts, prices
-- and positions are stored: no character names, no chat, no player GUIDs.
--
-- /odc          show what is recorded
-- /odc reset    delete all recorded batches (after running tools/uploader.py)

local DB_FORMAT = 1
local RECENT = 5 -- seconds a cast spell or used item counts as the cause of a loot window

local batch
local seen = {}           -- creature GUIDs already recorded as a level sighting this session
local lastSpell, lastSpellAt = nil, 0
local lastItem, lastItemAt = nil, 0
local questHolder = ""    -- who the quest frame is showing (set on detail/progress/complete)

local GetItemIDFromLink = function(link)
    return link and tonumber(link:match("item:(%d+)"))
end

local function guidInfo(guid)
    if not guid then return nil end
    local kind, _, _, _, _, id, spawn = strsplit("-", guid)
    if kind == "Creature" or kind == "Vehicle" then return "C", tonumber(id), spawn end
    if kind == "GameObject" then return "G", tonumber(id), spawn end
    if kind == "Item" then return "I" end
    if kind == "Player" then return "P" end
    return nil
end

local function holder(guid)
    local kind, id, spawn = guidInfo(guid)
    if (kind == "C" or kind == "G") and id then return kind .. ":" .. id .. ":" .. (spawn or "") end
    if kind == "I" then
        if lastItem and GetTime() - lastItemAt < RECENT then return "I:" .. lastItem end
        return "I:"
    end
    if kind == "P" then return "P" end
    return ""
end

local function where()
    local instanceID = select(8, GetInstanceInfo())
    local uiMap = C_Map and C_Map.GetBestMapForUnit and C_Map.GetBestMapForUnit("player")
    local x, y
    if uiMap then
        local pos = C_Map.GetPlayerMapPosition(uiMap, "player")
        if pos then x, y = pos:GetXY() end
    end
    return instanceID or "", uiMap or "",
        x and ("%.2f"):format(x * 100) or "", y and ("%.2f"):format(y * 100) or ""
end

local function push(...)
    if not batch then return end
    local t = {}
    for i = 1, select("#", ...) do
        local v = select(i, ...)
        t[i] = v == nil and "" or tostring(v)
    end
    batch.events[#batch.events + 1] = table.concat(t, "\t")
end

local function clean(s)
    return (tostring(s or ""):gsub("[\t,:|]", " "))
end

local function newBatchID()
    return ("%08x%08x%08x"):format(time(), math.random(0, 0x7fffffff), math.random(0, 0x7fffffff))
end

local function startBatch()
    OpenDataCollectorDB = OpenDataCollectorDB or {}
    local db = OpenDataCollectorDB
    db.format = DB_FORMAT
    db.batches = db.batches or {}
    local version, build = GetBuildInfo()
    batch = {
        id = newBatchID(),
        build = version .. "." .. build,
        locale = GetLocale(),
        faction = UnitFactionGroup("player"),
        started = time(),
        events = {},
    }
    db.batches[#db.batches + 1] = batch
end

-- ── loot ─────────────────────────────────────────────────────────────
-- L  t  source  map  uiMap  x  y  spell  playerLevel  entries
-- source: C:npc:spawn | G:object:spawn | I:item | F (fishing)
-- entries: item:count,... and $:copper for money

local MONEY = (Enum and Enum.LootSlotType and Enum.LootSlotType.Money) or LOOT_SLOT_MONEY or 2

local function onLootOpened()
    local fishing = IsFishingLoot and IsFishingLoot()
    local bySource, order = {}, {}
    for slot = 1, GetNumLootItems() do
        local isMoney = GetLootSlotType(slot) == MONEY
        local itemID = not isMoney and GetItemIDFromLink(GetLootSlotLink(slot))
        local sources = { GetLootSourceInfo(slot) }
        for k = 1, #sources, 2 do
            local guid, qty = sources[k], sources[k + 1]
            if guid then
                if not bySource[guid] then
                    bySource[guid] = {}
                    order[#order + 1] = guid
                end
                local list = bySource[guid]
                if isMoney then
                    list[#list + 1] = "$:" .. (qty or 0)
                elseif itemID then
                    list[#list + 1] = itemID .. ":" .. (qty or 1)
                end
            end
        end
    end
    local spell = (lastSpell and GetTime() - lastSpellAt < RECENT) and lastSpell or ""
    local map, uiMap, x, y = where()
    for _, guid in ipairs(order) do
        local src = fishing and "F" or holder(guid)
        if src ~= "" and src ~= "P" then
            push("L", time(), src, map, uiMap, x, y, spell, UnitLevel("player"), table.concat(bySource[guid], ","))
        end
    end
end

-- ── merchant ─────────────────────────────────────────────────────────
-- V  t  npc  spawn  map  uiMap  x  y  items
-- items: item:price:stackSize:available:costs,...   available -1 = unlimited
-- costs: item*count+item*count (extended cost), empty for gold-only

local function onMerchantShow()
    local kind, npc, spawn = guidInfo(UnitGUID("npc"))
    if kind ~= "C" then return end
    local items = {}
    for i = 1, GetMerchantNumItems() do
        local itemID = GetMerchantItemID(i)
        local _, _, price, quantity, numAvailable, _, _, extendedCost = GetMerchantItemInfo(i)
        if itemID then
            local costs = {}
            if extendedCost and GetMerchantItemCostInfo then
                for k = 1, GetMerchantItemCostInfo(i) or 0 do
                    local _, value, link = GetMerchantItemCostItem(i, k)
                    local costID = GetItemIDFromLink(link)
                    if costID then costs[#costs + 1] = costID .. "*" .. (value or 0) end
                end
            end
            items[#items + 1] = table.concat({ itemID, price or 0, quantity or 1, numAvailable or -1, table.concat(costs, "+") }, ":")
        end
    end
    local map, uiMap, x, y = where()
    push("V", time(), npc, spawn, map, uiMap, x, y, table.concat(items, ","))
end

-- ── quests ───────────────────────────────────────────────────────────
-- QA  t  quest  giver  map  uiMap  x  y  playerLevel
-- QT  t  quest  ender  map  uiMap  x  y  playerLevel  xp  money
-- giver/ender: C:npc:spawn | G:object:spawn | I:item | P (shared by a player) | empty

local function rememberQuestHolder()
    local guid = UnitGUID("npc")
    questHolder = guid and holder(guid) or ((lastItem and GetTime() - lastItemAt < RECENT) and ("I:" .. lastItem) or "")
end

local function onQuestAccepted(a, b)
    local questID = b or a
    if not questID then return end
    local map, uiMap, x, y = where()
    push("QA", time(), questID, questHolder, map, uiMap, x, y, UnitLevel("player"))
end

local function onQuestTurnedIn(questID, xp, money)
    if not questID then return end
    local map, uiMap, x, y = where()
    push("QT", time(), questID, questHolder, map, uiMap, x, y, UnitLevel("player"), xp or "", money or "")
end

-- ── trainer ──────────────────────────────────────────────────────────
-- T  t  npc  spawn  map  uiMap  x  y  services
-- services: createdItem:cost:name,...  (only what the trainer frame lists with its current filter)

local function onTrainerShow()
    local kind, npc, spawn = guidInfo(UnitGUID("npc"))
    if kind ~= "C" or not GetNumTrainerServices then return end
    local services = {}
    for i = 1, GetNumTrainerServices() do
        local name, _, category = GetTrainerServiceInfo(i)
        if name and category ~= "header" then
            local created = GetTrainerServiceItemLink and GetItemIDFromLink(GetTrainerServiceItemLink(i)) or ""
            services[#services + 1] = table.concat({ created or "", GetTrainerServiceCost(i) or 0, clean(name) }, ":")
        end
    end
    local map, uiMap, x, y = where()
    push("T", time(), npc, spawn, map, uiMap, x, y, table.concat(services, ","))
end

-- ── level sightings ──────────────────────────────────────────────────
-- S  t  npc  spawn  level

local function sight(unit)
    local guid = UnitGUID(unit)
    if not guid or seen[guid] then return end
    local kind, npc, spawn = guidInfo(guid)
    if kind ~= "C" or not npc then return end
    local level = UnitLevel(unit)
    if not level or level <= 0 then return end -- -1 is "??"
    seen[guid] = true
    push("S", time(), npc, spawn, level)
end

-- ── wiring ───────────────────────────────────────────────────────────

local function rememberContainerItem(bag, slot)
    local getID = (C_Container and C_Container.GetContainerItemID) or GetContainerItemID
    if getID then
        lastItem, lastItemAt = getID(bag, slot), GetTime()
    end
end
if C_Container and C_Container.UseContainerItem then
    hooksecurefunc(C_Container, "UseContainerItem", rememberContainerItem)
elseif UseContainerItem then
    hooksecurefunc("UseContainerItem", rememberContainerItem)
end

local handlers = {
    PLAYER_LOGIN = startBatch,
    LOOT_OPENED = onLootOpened,
    MERCHANT_SHOW = onMerchantShow,
    QUEST_DETAIL = rememberQuestHolder,
    QUEST_PROGRESS = rememberQuestHolder,
    QUEST_COMPLETE = rememberQuestHolder,
    QUEST_ACCEPTED = onQuestAccepted,
    QUEST_TURNED_IN = onQuestTurnedIn,
    TRAINER_SHOW = onTrainerShow,
    NAME_PLATE_UNIT_ADDED = sight,
    PLAYER_TARGET_CHANGED = function() sight("target") end,
    UPDATE_MOUSEOVER_UNIT = function() sight("mouseover") end,
    UNIT_SPELLCAST_SUCCEEDED = function(unit, _, spellID)
        if unit == "player" then lastSpell, lastSpellAt = spellID, GetTime() end
    end,
}

local frame = CreateFrame("Frame")
for event in pairs(handlers) do
    pcall(frame.RegisterEvent, frame, event) -- an event missing from this client is skipped, not fatal
end
frame:SetScript("OnEvent", function(_, event, ...)
    local ok, err = pcall(handlers[event], ...)
    if not ok then
        print(("OpenDataCollector: %s failed: %s"):format(event, tostring(err)))
    end
end)

SLASH_OPENDATACOLLECTOR1 = "/odc"
SlashCmdList.OPENDATACOLLECTOR = function(msg)
    local db = OpenDataCollectorDB or {}
    if msg == "reset" then
        db.batches = {}
        if batch then
            batch.events = {}
            batch.id = newBatchID() -- what was uploaded under the old id stays complete
            batch.started = time()
            db.batches[1] = batch
        end
        print("OpenDataCollector: recorded batches deleted.")
        return
    end
    local n, events = 0, 0
    for _, b in ipairs(db.batches or {}) do
        n = n + 1
        events = events + #b.events
    end
    print(("OpenDataCollector: %d batches, %d events (%d this session). /odc reset after uploading."):format(n, events, batch and #batch.events or 0))
end

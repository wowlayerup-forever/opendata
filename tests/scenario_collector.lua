-- Drive OpenDataCollector through one login session.
--   luajit tests/scenario_collector.lua <SavedVariables path> <session: 1|2|reset>
-- Session 1 plays a full set of events, session 2 logs in again on top of session 1's
-- SavedVariables and loots once, "reset" logs in and runs /odc reset.
dofile("tests/wowmock.lua")
local sv, session = arg[1], arg[2] or "1"
local S = wow.state

wow.load_savedvariables(sv)
wow.load_addon("tools/addons/OpenDataCollector/OpenDataCollector.lua", "OpenDataCollector")
wow.fire("PLAYER_LOGIN")

local g1 = wow.creature_guid(568, "00004A2B3C")   -- Shadowhide Warrior
local g2 = wow.creature_guid(568, "00004A2B3D")
local g3 = wow.creature_guid(568, "00004A2B3E")
local vendor = wow.creature_guid(1682, "0000ABCDEF")

local function loot(slots, fishing)
    S.loot = { slots = slots, fishing = fishing }
    wow.fire("LOOT_OPENED", false, false)
    S.loot = nil
    wow.advance(10)
end

if session == "1" then
    -- a corpse, then the same corpse opened again after taking the cloth
    loot({ { item = 2589, sources = { g1, 2 } }, { money = 125, sources = { g1, 125 } }, { item = 1075, sources = { g1, 1 } } })
    loot({ { item = 1075, sources = { g1, 1 } } })
    -- AoE loot: one slot fed by two corpses
    S.pos.x, S.pos.y = 0.5330, 0.4020
    loot({ { item = 2589, sources = { g2, 1, g3, 3 } } })
    -- skinning g2
    wow.fire("UNIT_SPELLCAST_SUCCEEDED", "player", "Cast-3-4410-0-1-8613-000", 8613)
    wow.advance(1)
    loot({ { item = 2318, sources = { g2, 1 } } })
    -- mining a Copper Vein
    wow.fire("UNIT_SPELLCAST_SUCCEEDED", "player", "Cast-3-4410-0-1-2575-000", 2575)
    loot({ { item = 2770, sources = { wow.object_guid(1731, "0000112233"), 2 } } })
    -- fishing: the source is the bobber, IsFishingLoot says so
    S.pos.x, S.pos.y = 0.40, 0.60
    loot({ { item = 6303, sources = { wow.object_guid(35591, "0000B0BBE5"), 1 } } }, true)
    -- opening a clam from the bags
    S.bags[0] = { [1] = 5523 }
    C_Container.UseContainerItem(0, 1)
    loot({ { item = 5498, sources = { "Item-4410-0-40000000ABCD", 1 } } })

    -- vendor
    S.pos.x, S.pos.y = 0.105, 0.602
    S.units.npc = { guid = vendor, level = 20 }
    S.merchant = {
        { id = 159, price = 25 },
        { id = 4470, price = 38, available = 5 },
        { id = 2901, price = 0, costs = { { 2589, 3 } } },
    }
    wow.fire("MERCHANT_SHOW")
    -- quest accept (QUEST_ACCEPTED with questLogIndex, questID) and turn-in
    wow.fire("QUEST_DETAIL")
    wow.fire("QUEST_ACCEPTED", 5, 303)
    wow.advance(60)
    wow.fire("QUEST_COMPLETE")
    wow.fire("QUEST_TURNED_IN", 303, 2450, 2500)
    -- trainer, with a header row
    S.trainer = {
        { name = "Blacksmithing", kind = "header" },
        { name = "Rough Sharpening Stone", cost = 100, creates = 2862 },
        { name = "Smelt Copper", cost = 50, creates = 2840 },
    }
    wow.fire("TRAINER_SHOW")
    S.units.npc = nil

    -- level sightings: target, nameplate, the same spawn again on mouseover, and a skull
    S.units.target = { guid = g1, level = 19 }
    wow.fire("PLAYER_TARGET_CHANGED")
    S.units.nameplate1 = { guid = g3, level = 20 }
    wow.fire("NAME_PLATE_UNIT_ADDED", "nameplate1")
    S.units.mouseover = { guid = g1, level = 19 }
    wow.fire("UPDATE_MOUSEOVER_UNIT")
    S.units.nameplate2 = { guid = wow.creature_guid(568, "00004A2B3F"), level = -1 }
    wow.fire("NAME_PLATE_UNIT_ADDED", "nameplate2")

    SlashCmdList.OPENDATACOLLECTOR("")
elseif session == "2" then
    loot({ { item = 2589, sources = { wow.creature_guid(568, "00004A2B40"), 1 } } })
elseif session == "reset" then
    local old = {}
    for _, b in ipairs(OpenDataCollectorDB.batches) do old[b.id] = true end
    SlashCmdList.OPENDATACOLLECTOR("reset")
    assert(not old[OpenDataCollectorDB.batches[1].id], "reset must not reuse an uploaded batch id")
    local db = OpenDataCollectorDB
    assert(#db.batches == 1, "reset should leave only the current batch")
    assert(#db.batches[1].events == 0, "reset should empty the current batch")
end

wow.write_savedvariables(sv, { "OpenDataCollectorDB" })
-- the SavedVariable itself is nil on a first login; the addon reads it to initialise
wow.report({ OpenDataCollectorDB = true })

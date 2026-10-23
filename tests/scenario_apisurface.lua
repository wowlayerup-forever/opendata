-- Drive APISurface through one login.
--   luajit tests/scenario_apisurface.lua <SavedVariables path> [nodocs]
dofile("tests/wowmock.lua")
local sv, mode = arg[1], arg[2]
local S = wow.state

S.addons = {
    { name = "APISurface", loaded = true },
    { name = "OpenDataCollector", loaded = true },
    { name = "Blizzard_APIDocumentation", loaded = true },
    { name = "SomeDisabledAddon", loaded = false },
}
if mode ~= "nodocs" then
    S.docs = {
        {
            Name = "MapUI", Namespace = "C_Map",
            Functions = {
                {
                    Name = "GetPlayerMapPosition", Type = "Function",
                    Arguments = {
                        { Name = "uiMapID", Type = "number", Nilable = false },
                        { Name = "unitToken", Type = "cstring", Nilable = false, Default = "player" },
                    },
                    Returns = { { Name = "position", Type = "vector2", Mixin = "Vector2DMixin", Nilable = true } },
                    Documentation = { "Only works for the player and party members.", "Second line with a \"quote\"." },
                },
                { Name = "GetBestMapForUnit", Type = "Function",
                  Arguments = { { Name = "unitToken", Type = "cstring", Nilable = false } },
                  Returns = { { Name = "uiMapID", Type = "number", Nilable = true } } },
                { Name = "NotInThisFlavor", Type = "Function" },
            },
            Events = {
                { Name = "NewWmoChunk", LiteralName = "NEW_WMO_CHUNK", Payload = {} },
            },
        },
        {
            Name = "Loot",
            Functions = { { Name = "GetLootSourceInfo", Type = "Function",
                            Arguments = { { Name = "slot", Type = "luaIndex", Nilable = false } } } },
            Events = {
                { Name = "LootOpened", LiteralName = "LOOT_OPENED",
                  Payload = { { Name = "autoLoot", Type = "bool", Nilable = false },
                              { Name = "isFromItem", Type = "bool", Nilable = false } } },
                { Name = "LootItems", LiteralName = "LOOT_READY",
                  Payload = { { Name = "slots", Type = "table", InnerType = "number", Nilable = false } } },
            },
        },
    }
end
-- an undocumented C_ namespace, as the client has many
_G.C_Undocumented = { DoThing = function() end, notAFunction = 3 }

wow.load_addon("tools/addons/APISurface/APISurface.lua", "APISurface")
wow.fire("PLAYER_LOGIN")
wow.write_savedvariables(sv, { "APISurfaceDB" })
-- without documentation the addon looks for APIDocumentation and finds nothing
wow.report(mode == "nodocs" and { APIDocumentation = true } or {})

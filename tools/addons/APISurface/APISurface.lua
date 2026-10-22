-- APISurface: on login, record every global function, every function in a C_* namespace,
-- and everything Blizzard_APIDocumentation describes, into APISurfaceDB.
-- tools/import_api.py turns the SavedVariables file into data/api/<build>/*.jsonl.
--
-- Run it with every other addon disabled: anything another addon puts in _G would
-- otherwise show up as an undocumented function. Loaded third-party addons are recorded
-- so the importer can refuse a contaminated dump.

local frame = CreateFrame("Frame")
frame:RegisterEvent("PLAYER_LOGIN")

local LoadAddOnFn = (C_AddOns and C_AddOns.LoadAddOn) or LoadAddOn
local IsAddOnLoadedFn = (C_AddOns and C_AddOns.IsAddOnLoaded) or IsAddOnLoaded
local GetNumAddOnsFn = (C_AddOns and C_AddOns.GetNumAddOns) or GetNumAddOns
local GetAddOnInfoFn = (C_AddOns and C_AddOns.GetAddOnInfo) or GetAddOnInfo

local function copyFields(list)
    if type(list) ~= "table" then return nil end
    local out = {}
    for i, a in ipairs(list) do
        out[i] = {
            name = a.Name,
            type = a.Type,
            innerType = a.InnerType,
            mixin = a.Mixin,
            nilable = a.Nilable and true or nil,
            default = a.Default,
        }
    end
    if #out == 0 then return nil end
    return out
end

local function copyDoc(doc)
    if type(doc) == "string" then return { doc } end
    if type(doc) ~= "table" or #doc == 0 then return nil end
    local out = {}
    for i, line in ipairs(doc) do out[i] = tostring(line) end
    return out
end

local function lookup(namespace, name)
    if namespace then
        local ns = _G[namespace]
        return type(ns) == "table" and type(ns[name]) == "function"
    end
    return type(_G[name]) == "function"
end

local function thirdPartyAddOns()
    local out = {}
    if not (GetNumAddOnsFn and GetAddOnInfoFn and IsAddOnLoadedFn) then return out end
    for i = 1, GetNumAddOnsFn() do
        local name = GetAddOnInfoFn(i)
        if name and name ~= "APISurface" and IsAddOnLoadedFn(i) and not name:match("^Blizzard_") then
            out[#out + 1] = name
        end
    end
    table.sort(out)
    return out
end

local function dump()
    local version, build, date, interface = GetBuildInfo()
    local functions, events = {}, {}
    local docNamespaces = {}

    if LoadAddOnFn then
        pcall(LoadAddOnFn, "Blizzard_APIDocumentationGenerated")
        pcall(LoadAddOnFn, "Blizzard_APIDocumentation")
    end

    local systems = APIDocumentation and APIDocumentation.systems
    if type(systems) == "table" then
        for _, sys in ipairs(systems) do
            local ns = sys.Namespace
            if ns then docNamespaces[ns] = true end
            for _, fn in ipairs(sys.Functions or {}) do
                if fn.Name then
                    local full = ns and (ns .. "." .. fn.Name) or fn.Name
                    functions[full] = {
                        namespace = ns,
                        system = sys.Name,
                        documented = true,
                        present = lookup(ns, fn.Name),
                        arguments = copyFields(fn.Arguments),
                        returns = copyFields(fn.Returns),
                        documentation = copyDoc(fn.Documentation),
                    }
                end
            end
            for _, ev in ipairs(sys.Events or {}) do
                if ev.LiteralName then
                    events[ev.LiteralName] = {
                        system = sys.Name,
                        payload = copyFields(ev.Payload),
                        documentation = copyDoc(ev.Documentation),
                    }
                end
            end
        end
    end

    local function mark(full, namespace)
        if not functions[full] then
            functions[full] = { namespace = namespace, documented = false, present = true }
        end
    end

    for k, v in pairs(_G) do
        if type(k) == "string" then
            if type(v) == "function" then
                mark(k, nil)
            elseif type(v) == "table" and (k:match("^C_") or docNamespaces[k]) then
                for k2, v2 in pairs(v) do
                    if type(k2) == "string" and type(v2) == "function" then
                        mark(k .. "." .. k2, k)
                    end
                end
            end
        end
    end

    APISurfaceDB = {
        format = 1,
        version = version,
        build = build,
        buildDate = date,
        interface = interface,
        locale = GetLocale(),
        hasDocumentation = type(systems) == "table",
        thirdPartyAddOns = thirdPartyAddOns(),
        functions = functions,
        events = events,
    }

    local nf, ne = 0, 0
    for _ in pairs(functions) do nf = nf + 1 end
    for _ in pairs(events) do ne = ne + 1 end
    print(("APISurface: recorded %d functions and %d events for %s.%s. /reload or log out to write SavedVariables."):format(nf, ne, version, build))
end

frame:SetScript("OnEvent", function(self, event)
    self:UnregisterEvent(event)
    local ok, err = pcall(dump)
    if not ok then print("APISurface: dump failed: " .. tostring(err)) end
end)

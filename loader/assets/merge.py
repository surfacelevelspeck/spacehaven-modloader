
import os
import copy
import lxml.etree

import loader.assets.library
from .library import PATCHABLE_XML_FILES, PATCHABLE_CIM_FILES
from .explode import Texture

import ui.log

def _detect_textures(coreLibrary, modLibrary, mod):
    textures_path = os.path.join(mod, 'textures')
    if not os.path.isdir(textures_path):
        return {}

    mapping_n_region = {}
    modded_textures = {}
    seen_textures = set()

    def _add_texture(region_id):
        filename = region_id + '.png'
        if filename in seen_textures:
            return

        path = os.path.join(textures_path, filename)
        if not os.path.isfile(path):
            ui.log.log("  ERROR MISSING {}...".format(filename))
            ui.log.log("  ERROR MISSING {}...".format(filename))
            ui.log.log("  ERROR MISSING {}...".format(filename))
            return

        ui.log.log("  Found {}...".format(filename))
        if int(region_id) > coreLibrary['_last_core_region_id']:
            # adding a new texture, this gets tricky as they have to have consecutive numbers. 
            core_region_id = str(coreLibrary['_next_region_id'])
            mapping_n_region[region_id] = core_region_id
            coreLibrary['_next_region_id'] += 1
        else:
            core_region_id = region_id

        seen_textures.add(filename)
        modded_textures[core_region_id] = {
            'mapped_from_id' : region_id,
            'filename' : filename,
            'path' : path, 
            }

    for filename in os.listdir(textures_path):
        # also scan the directory for overwriting existing core textures
        if not filename.endswith('.png'):
            continue
        try:
            int(filename.split('.')[0])
        except:
            # wrong format
            continue
        _add_texture(filename.split('.')[0])

    if 'library/textures' not in modLibrary:
        # no textures.xml file, we're done
        return modded_textures

    #FIXME verify that there's only one file
    textures_mod = modLibrary['library/textures'][0]

    for texture_pack in textures_mod.xpath("//t[@i]"):
        cim_id = texture_pack.get('i')
        coreLibrary['_custom_textures_cim'][cim_id] = texture_pack.attrib

    for region in textures_mod.xpath("//re[@n]"):
        region_id = region.get('n')
        _add_texture(region_id)

    if not mapping_n_region:
        # no custom mod textures, no need to remap ids 
        return modded_textures

    for animation_chunk in modLibrary['library/animations']:
        for asset in animation_chunk.xpath("//assetPos[@a]"):
            mod_local_id = asset.get('a')
            if mod_local_id not in mapping_n_region:
                continue
            new_id = mapping_n_region[mod_local_id]
            ui.log.log("  Mapping animation 'assetPos' {} to {}...".format(mod_local_id, new_id))
            asset.set('a', new_id)

    for asset in textures_mod.xpath("//re[@n]"):
        mod_local_id = asset.get('n')
        if mod_local_id not in mapping_n_region:
            continue
        new_id = mapping_n_region[mod_local_id]
        ui.log.log("  Mapping texture 're' {} to {}...".format(mod_local_id, new_id))
        asset.set('n', new_id)

    return modded_textures


def mods(corePath, modPaths):
    # Load the core library files
    coreLibrary = {}
    def _core_path(filename):
        return os.path.join(corePath, filename.replace('/', os.sep))

    def buildLibrary(location: str):
        """Build up a library dict of files in `location`"""
        def _mod_path(filename):
            return os.path.join(mod, filename.replace('/', os.sep))
        location_library = {}
        try:
            location_files = [location + '/' + mod_file for mod_file in os.listdir(_mod_path(location))]
        except FileNotFoundError:
            location_files = []

        # we allow breaking down mod xml files into smaller pieces for readability
        for target in PATCHABLE_XML_FILES:
            targetInLocation = target.replace('library', location)
            for mod_file in location_files:
                if not mod_file.startswith(targetInLocation): continue
                if target not in location_library:  location_library[target] = []

                ui.log.log("    {} <= {}".format(target, mod_file))
                with open(_mod_path(mod_file)) as f:
                    location_library[target].append(lxml.etree.parse(f, parser=lxml.etree.XMLParser(remove_comments=True)))

            mod_file = _mod_path(target)
            # try again with the extension ?
            if not os.path.exists(mod_file):
                mod_file += '.xml'
                if not os.path.exists(mod_file):
                    continue
        return location_library

    for filename in PATCHABLE_XML_FILES:
        with open(_core_path(filename), 'rb') as f:
            coreLibrary[filename] = lxml.etree.parse(f, parser=lxml.etree.XMLParser(recover=True))

    # find the last region in the texture file and remember its index
    # we will need this to add mod textures with consecutive indexes...
    coreLibrary['_last_core_region_id'] = int(coreLibrary['library/textures'].find("//re[@n][last()]").get('n'))
    coreLibrary['_next_region_id'] = coreLibrary['_last_core_region_id'] + 1
    coreLibrary['_all_modded_textures'] = {}
    coreLibrary['_custom_textures_cim'] = {}

    # Merge in modded files
    for mod in modPaths:
        ui.log.updateLaunchState("Installing {}".format(os.path.basename(mod)))

        ui.log.log("  Loading mod {}...".format(mod))

        # Load the mod's library
        modLibrary = buildLibrary('library')

        doMerges(coreLibrary, modLibrary, mod)

    # Do patches after merges to avoid clobbers
    for mod in modPaths:
        ui.log.updateLaunchState(f"Patching {os.path.basename(mod)}")
        ui.log.log(f"  Loading patches {mod}...")
        modPatchesLibrary = buildLibrary('patches')
        doPatches(coreLibrary, modPatchesLibrary, mod)

    ui.log.updateLaunchState("Updating XML")

    # Write out the new base library
    for filename in PATCHABLE_XML_FILES:        
        with open(_core_path(filename), "wb") as f:
            f.write(lxml.etree.tostring(coreLibrary[filename], pretty_print=True, encoding="UTF-8"))

    ui.log.updateLaunchState("Packing textures")
    # add or overwrite textures from mods. This is done after all the XML has been merged into the core "textures" file
    cims = {}
    reexport_cims = {}
    extra_assets = []

    for region in coreLibrary['library/textures'].xpath("//re[@n]"):
        name = region.get("n")

        if name not in coreLibrary['_all_modded_textures']:
            continue

        png_file = coreLibrary['_all_modded_textures'][name]['path']

        page = region.get("t")
        if not page in cims:
            cim_name = '{}.cim'.format(page)
            kwargs = {'create': False}
            # TODO better cross checking of texture packs
            if 'library/' + cim_name not in PATCHABLE_CIM_FILES:
                kwargs['create'] = True
                kwargs['width'] = coreLibrary['_custom_textures_cim'][page]['w']
                kwargs['height'] = coreLibrary['_custom_textures_cim'][page]['h']
                extra_assets.append('library/' + cim_name)
            cims[page] = Texture(os.path.join(corePath, 'library', cim_name), **kwargs)

            reexport_cims[page] = set()

        # write back the cim file as png for debugging
        reexport_cims[page].add(os.path.dirname(png_file))

        x = int(region.get("x"))
        y = int(region.get("y"))
        w = int(region.get("w"))
        h = int(region.get("h"))

        ui.log.log("  Patching {}.cim...".format(page))
        cims[page].pack_png(png_file, x, y, w, h)

    # cims contains only the textures files that have actually been modified
    for page in cims:
        ui.log.log("  Writing {}.cim...".format(page))
        cims[page].export_cim(os.path.join(corePath, 'library', '{}.cim'.format(page)))
        for path in reexport_cims[page]:
            cims[page].export_png(os.path.join(path, 'modded_cim_{}.png'.format(page)))

    return extra_assets



def AttributeSet(patchArgs):
    """Set the attribute on the node, adding if not present"""
    elem : lxml.etree._Element
    currentCoreLibElems = patchArgs["coreLibElems"]
    attribute = patchArgs["attribute"].text
    value = patchArgs["value"]
    for elem in currentCoreLibElems: elem.set(attribute, value.text)


def AttributeAdd(patchArgs):
    """Adds the attribute to the node IFF the attribute name is not already present"""
    elem : lxml.etree._Element
    currentCoreLibElems = patchArgs["coreLibElems"]
    attribute = patchArgs["attribute"].text

    for elem in currentCoreLibElems:
        if elem.get(attribute, None) is not None:
            raise KeyError(f"Attribute '{attribute}' already exists")
        elem.set(attribute, value.text)


def AttributeRemove(patchArgs):
    """Remove the attribute from the node"""
    ui.log.log(f"    WARNING: REMOVING ATTRIBUTES MAY BREAK THE GAME")
    elem : lxml.etree._Element
    currentCoreLibElems = patchArgs["coreLibElems"]
    attribute = patchArgs["attribute"].text
    for elem in currentCoreLibElems: elem.attrib.pop(attribute)


def AttributeMath(patchArgs):
    """Set the attribute on the node, via math"""
    elem : lxml.etree._Element
    currentCoreLibElems = patchArgs["coreLibElems"]
    attribute = patchArgs["attribute"].text
    value = patchArgs["value"]
    opType = value.get("opType", None)
    valueFloat = float(value.text)
    for elem in currentCoreLibElems:
        startVal = float(elem.get(attribute, 0))
        isFloat = "." in elem.get(attribute, 0)
        if opType == "add":
            newVal = startVal + valueFloat
        elif opType == "subtract":
            newVal = startVal - valueFloat
        elif opType == "multiply":
            newVal = startVal * valueFloat
        elif opType == "divide":
            newVal = startVal / valueFloat
        else:
            raise AttributeError("Unknown opType")

        if isFloat:
            elem.set(attribute, f"{newVal:.1f}")
        else:
            newVal = int(newVal)
            elem.set(attribute, f"{newVal}")


def NodeAdd(patchArgs):
    """Adds a provided child node to the selected node"""
    elem : lxml.etree._Element
    parent: lxml.etree._Element
    currentCoreLibElems = patchArgs["coreLibElems"]
    value = patchArgs["value"]
    for elem in currentCoreLibElems:
        lastelemIDX = len(elem.getchildren())
        elem.insert(lastelemIDX + 1, copy.deepcopy(value[0]))


def NodeInsert(patchArgs):
    """Adds a provided sibling node to the selected node"""
    elem : lxml.etree._Element
    parent: lxml.etree._Element
    currentCoreLibElems = patchArgs["coreLibElems"]
    value = patchArgs["value"]
    for elem in currentCoreLibElems:
        parent = elem.find('./..')
        elemIDX = parent.index(elem)
        parent.insert(elemIDX + 1, copy.deepcopy(value[0]))


def NodeRemove(patchArgs):
    """Deletes the selected node"""
    elem : lxml.etree._Element
    parent: lxml.etree._Element
    currentCoreLibElems = patchArgs["coreLibElems"]
    for elem in currentCoreLibElems:
        parent = elem.find('./..')
        parent.remove(elem)


def NodeReplace(patchArgs):
    """Replaces the selected node with the provided node"""
    elem : lxml.etree._Element
    parent: lxml.etree._Element
    currentCoreLibElems = patchArgs["coreLibElems"]
    value = patchArgs["value"]
    for elem in currentCoreLibElems:
        parent = elem.find('./..')
        parent.replace(elem, copy.deepcopy(value[0]))


# Default case function
def BadOp(patchArgs):
    raise SyntaxError(f"BAD PATCH OPERATION")


patchDispatcher = {
    "AttributeSet" :    AttributeSet,
    "AttributeAdd" :    AttributeAdd,
    "AttributeRemove" : AttributeRemove,
    "AttributeMath" :   AttributeMath,
    "Add":              NodeAdd,
    "Insert":           NodeInsert,
    "Remove":           NodeRemove,
    "Replace":          NodeReplace,
}


def doPatches(coreLib, modLib: dict, mod: str):
    # Pretyping
    patchList : lxml.etree._ElementTree
    patchOperation : lxml.etree._Element

    # Helper functions
    def doPatchType(patch: lxml.etree._Element, location: str):
        """Execute a single patch. Provided to reduce indentation level"""
        # Pretyping
        currentCoreLib : lxml.etree._ElementTree
        patchOperation : lxml.etree._Element
        value: lxml.etree._Element

        pType =  patch.attrib["Class"]
        xpath = patch.find('xpath').text
        currentCoreLibElems = coreLib[location].xpath(xpath)

        ui.log.log(f"    XPATH => {location:>15}: {pType:18}{xpath}")
        if len(currentCoreLibElems) == 0:
            ui.log.log(f"    Unable to perform patch. XPath found no results {xpath}")
            return

        patchArgs = {
            "value":        patch.find('value'),
            "attribute":    patch.find("attribute"),     # Defer exception throw to later.
            "coreLibElems": currentCoreLibElems,
        }

        patchDispatcher.get(pType,BadOp)(patchArgs)

    # Execution
    for location in modLib:
        for patchList in modLib[location]:
            if patchList.find("Noload") is not None:
                continue
            for patchOperation in patchList.getroot():
                try:
                    doPatchType(patchOperation, location)
                except Exception as e:
                    uri = patchOperation.base
                    line = patchOperation.sourceline
                    ui.log.log(f"    Failed to apply patch operation {uri}:{line}")
                    ui.log.log(f"      Reason: {repr(e)}")
                    raise SyntaxError("Issue in patch operation. Check logs for info.") from None


def doMerges(coreLib, modLib, mod: str):
    """Do merge-based modding sequence"""
    def mergeShim(file: str, xpath: str, idAttribute: str):
        '''Shim to reduce function call complexity'''
        mergeDefinitions(coreLib, modLib, file, xpath, idAttribute)

    def mergeAbortMessage(filename: str):
        """Shim to standardize error message"""
        ui.log.log(f"    No merges needed: {filename}")

    # Lookup table for all nodes in library/haven based on element and the expected ID format
    havenIDLookUpTable = {
        "/data/BackPack": "mid",
        "/data/BackStory": "id",
        "/data/CelestialObject": "id",
        "/data/Character": "cid",
        "/data/CharacterCondition": "id",
        "/data/CharacterSet": "cid",
        "/data/CharacterTrait": "id",
        "/data/CostGroup": "id",
        "/data/Craft": "cid",
        "/data/DataLog": "id",
        "/data/DataLogFragment": "id",
        "/data/DefaultStuff": "id",
        "/data/DialogChoice": "id",
        "/data/DifficultySettings": "id",
        "/data/Effect": "id",
        "/data/Element": "mid",
        "/data/Encounter": "id",
        "/data/Faction": "id",
        "/data/GOAPAction": "id",
        "/data/IdleAnim": "id",
        "/data/IsoFX": "id",
        "/data/Item": "mid",
        "/data/MainCat": "id",
        "/data/Monster": "cid",
        "/data/Notes": "id",
        "/data/ObjectiveCollection": "nid",
        "/data/PersonalitySettings": "id",
        "/data/Plan": "id",
        "/data/Product": "eid",
        "/data/RandomShip": "id",
        "/data/Randomizer": "id",
        "/data/Room": "rid",
        "/data/Sector": "id",
        "/data/Ship": "rid",
        "/data/SubCat": "id",
        "/data/TradingValues": "id",
    }

    # Do an element-wise merge (replacing conflicts)
    currentFile = "library/haven"
    if currentFile in modLib:
        for path, idText in havenIDLookUpTable.items(): mergeShim(currentFile, path, idText)
    else: mergeAbortMessage(currentFile)

    currentFile = "library/texts"
    if currentFile in modLib:
        mergeShim(currentFile, "/t", idAttribute="id")
    else: mergeAbortMessage(currentFile)

    # do that before merging animations and textures because references might have to be remapped!
    coreLib['_all_modded_textures'].update(_detect_textures(coreLib, modLib, mod))

    # this way the last mod loaded will overwrite previous textures
    #FIXME reimplement this test
    #if region_id in all_modded_textures:
    #    ui.log.log("  ERROR CONFLICT {}...".format(filename))
    #    ui.log.log("  ERROR CONFLICT {}...".format(filename))
    #    ui.log.log("  ERROR CONFLICT {}...".format(filename))
    #    continue


    currentFile = "library/animations"
    if currentFile in modLib:
        mergeShim(currentFile, "/AllAnimations/animations", "n")
    else: mergeAbortMessage(currentFile)

    currentFile = "library/textures"
    if currentFile in modLib:
        mergeShim(currentFile, "/AllTexturesAndRegions/regions", "n")
    else: mergeAbortMessage(currentFile)


def mergeDefinitions(baseLibrary, modLibrary, file, xpath, idAttribute):
    if not file in modLibrary:
        ui.log.log("    {}: Not present".format(file))
        return

    try:
        baseRoot = baseLibrary[file].xpath(xpath)[0]
    except IndexError:
        #that's a big error if we can't find it in the core!
        ui.log.log("    {}: ERROR CORE NOTHING AT {}".format(file, xpath))
        return

    for mod_xml in modLibrary[file]:
        try:
            modRoot = mod_xml.xpath(xpath)[0]
        except:
            continue

        merged = 0
        for element in list(modRoot):
            conflicts = baseRoot.xpath("*[@{}='{}']".format(idAttribute, element.get(idAttribute)))

            for conflict in conflicts:
                baseRoot.remove(conflict)

            baseRoot.append(copy.deepcopy(element))
            merged += 1

        if merged:
            # TODO add source filename
            ui.log.log("    {}: Merged {} elements into {}".format(file, merged, xpath))

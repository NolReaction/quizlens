"""Exact answers for explicit periodic-table symbol questions."""

import re

# In atomic-number order. JINR names: https://www.inr.ru/rus/2022/aksenov.pdf
# IUPAC symbols: https://iupac.org/what-we-do/periodic-table-of-elements/
_ELEMENTS = """
водород hydrogen H
гелий helium He
литий lithium Li
бериллий beryllium Be
бор boron B
углерод carbon C
азот nitrogen N
кислород oxygen O
фтор fluorine F
неон neon Ne
натрий sodium Na
магний magnesium Mg
алюминий aluminium Al
кремний silicon Si
фосфор phosphorus P
сера sulfur S
хлор chlorine Cl
аргон argon Ar
калий potassium K
кальций calcium Ca
скандий scandium Sc
титан titanium Ti
ванадий vanadium V
хром chromium Cr
марганец manganese Mn
железо iron Fe
кобальт cobalt Co
никель nickel Ni
медь copper Cu
цинк zinc Zn
галлий gallium Ga
германий germanium Ge
мышьяк arsenic As
селен selenium Se
бром bromine Br
криптон krypton Kr
рубидий rubidium Rb
стронций strontium Sr
иттрий yttrium Y
цирконий zirconium Zr
ниобий niobium Nb
молибден molybdenum Mo
технеций technetium Tc
рутений ruthenium Ru
родий rhodium Rh
палладий palladium Pd
серебро silver Ag
кадмий cadmium Cd
индий indium In
олово tin Sn
сурьма antimony Sb
теллур tellurium Te
иод iodine I
ксенон xenon Xe
цезий caesium Cs
барий barium Ba
лантан lanthanum La
церий cerium Ce
празеодим praseodymium Pr
неодим neodymium Nd
прометий promethium Pm
самарий samarium Sm
европий europium Eu
гадолиний gadolinium Gd
тербий terbium Tb
диспрозий dysprosium Dy
гольмий holmium Ho
эрбий erbium Er
тулий thulium Tm
иттербий ytterbium Yb
лютеций lutetium Lu
гафний hafnium Hf
тантал tantalum Ta
вольфрам tungsten W
рений rhenium Re
осмий osmium Os
иридий iridium Ir
платина platinum Pt
золото gold Au
ртуть mercury Hg
таллий thallium Tl
свинец lead Pb
висмут bismuth Bi
полоний polonium Po
астат astatine At
радон radon Rn
франций francium Fr
радий radium Ra
актиний actinium Ac
торий thorium Th
протактиний protactinium Pa
уран uranium U
нептуний neptunium Np
плутоний plutonium Pu
америций americium Am
кюрий curium Cm
берклий berkelium Bk
калифорний californium Cf
эйнштейний einsteinium Es
фермий fermium Fm
менделевий mendelevium Md
нобелий nobelium No
лоуренсий lawrencium Lr
резерфордий rutherfordium Rf
дубний dubnium Db
сиборгий seaborgium Sg
борий bohrium Bh
хассий hassium Hs
мейтнерий meitnerium Mt
дармштадтий darmstadtium Ds
рентгений roentgenium Rg
коперниций copernicium Cn
нихоний nihonium Nh
флеровий flerovium Fl
московий moscovium Mc
ливерморий livermorium Lv
теннессин tennessine Ts
оганесон oganesson Og
""".strip().splitlines()

_QUESTION = re.compile(
    r"(?:(?:какой|каков) (?:химический символ|символ химического элемента) (?:(?:у|для) )?"
    r"|как обозначается химический элемент "
    r"|(?:what is|what's) (?:the )?(?:chemical|element) symbol (?:for|of) )([a-zа-я]+)"
)
_GENITIVE_ENDINGS = {"й": "я", "ь": "я", "о": "а", "а": "ы"}
_GENITIVE_EXCEPTIONS = {"медь": "меди", "ртуть": "ртути", "свинец": "свинца"}


def _genitive(name):
    if name in _GENITIVE_EXCEPTIONS:
        return _GENITIVE_EXCEPTIONS[name]
    ending = _GENITIVE_ENDINGS.get(name[-1])
    return name[:-1] + ending if ending else name + "а"


_SYMBOLS = {"йод": "I", "йода": "I", "гассий": "Hs", "гассия": "Hs",
            "aluminum": "Al", "cesium": "Cs", "sulphur": "S"}
for _entry in _ELEMENTS:
    _russian, _english, _symbol = _entry.split()
    _SYMBOLS.update({_russian: _symbol, _genitive(_russian): _symbol, _english: _symbol})


def requested_symbol(question):
    normalized = " ".join(question.casefold().replace("ё", "е").replace("’", "'").split())
    match = _QUESTION.fullmatch(normalized.rstrip(" ?.!"))
    return _SYMBOLS.get(match[1]) if match else None

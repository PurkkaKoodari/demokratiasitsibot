from typing import TypedDict, cast

from typings import AppContext

lang_icons = {
    "fi": "🇫🇮 FI",
    "en": "🇬🇧 EN",
}


class Locale(TypedDict):
    welcome: str
    choose_lang: str
    lang_set: str
    enter_code: str
    code_placeholder: str
    invalid_code: str
    used_code: str
    area: str
    help: str
    absent: str
    unabsent: str
    poll_confirm: str
    election_confirm: str
    poll_voted: str
    election_voted: str
    init_notifs_on: str
    init_notifs_off: str
    init_title: str
    init_title_placeholder: str
    init_title_length: str
    init_desc: str
    init_desc_placeholder: str
    init_desc_length: str
    init_checkup: str
    init_send: str
    init_edit_title: str
    init_edit_desc: str
    init_editing_title: str
    init_editing_desc: str
    init_cancel: str
    init_sent: str
    init_canceled: str
    init_published: str
    init_unconstitutional: str
    init_shitpost: str
    init_in_review: str
    init_banned: str
    init_notif: str
    init_second: str
    init_seconded: str
    init_broken: str


locale: dict[str, Locale] = {
    "fi": {
        "welcome": "<b>Tervetuloa Demokratiasitseille!</b>\n<b>Welcome to the Democracy Sitsit!</b>\n\nValitse kieli:\nChoose language:",
        "choose_lang": "Valitse kieli:\nChoose language:",
        "lang_set": "Kieli asetettu!\n\nYou can always change the language with /language.",
        "enter_code": "Syötä sitseille saapuessasi saamasi koodi.",
        "code_placeholder": "Koodi",
        "invalid_code": "Koodi on virheellinen. Tarkista vielä oikeinkirjoitus ja pyydä tarvittaessa apua sitsien henkilökunnalta.",
        "used_code": "Koodia on jo käytetty toisella Telegram-käyttäjällä! Pyydä apua sitsien henkilökunnalta, jos haluat siirtää koodin tunnuksellesi.",
        "area": "Paikkasi on <b>pöydässä {area}</b>. Voit valita paikkasi tässä pöydässä vapaasti.",
        "help": "Tervetuloa! {area}\n\nSitsien aikana äänestykset lähetetään sinulle tämän Telegram-botin kautta. Voit tarkistaa nykyisen äänestyksen komennolla /current.\n\nSitsien aikana voit tehdä kansalaisaloitteita komennolla /initiative. Voit lukea kansalaisaloitteita komennolla /initiatives.\n\n{initnotif}\n\nJos poistut sitseiltä, kirjoita komento /absent.",
        "absent": "Sinut on merkitty poissaolevaksi. Voit palata sitseille lähettämällä minkä tahansa komennon.",
        "unabsent": "Sinut on merkitty paikallaolevaksi.",
        "poll_confirm": "{title}\n\nHaluatko varmasti äänestää vaihtoehtoa {option}?",
        "election_confirm": "{title}\n\nHaluatko varmasti äänestää ehdokasta {option}?",
        "poll_voted": "{title}\n\nÄänestit vaihtoehtoa {option}.",
        "election_voted": "{title}\n\nÄänestit ehdokasta {option}.",
        "init_notifs_on": "Saat tällä hetkellä ilmoituksen kaikista uusista kansalaisaloitteista. Voit kytkeä tämän pois käytöstä komennolla /inotifications.",
        "init_notifs_off": "Et saa tällä hetkellä ilmoituksia uusista kansalaisaloitteista. Voit kytkeä ne takaisin päälle komennolla /inotifications.",
        "init_title": "Tervetuloa kansalaisaloitteen laadintaan!\n\nMieti tarkasti, mitä ehdotat \u2013 kansalaisaloitteet tarkastetaan ennen julkaisua, ja roskapostin lähettäminen tarkastettavaksi johtaa aloitteiden luontikieltoon.\n\nKeksi ensin osuva otsikko aloitteellesi! (max. {length} merkkiä)",
        "init_title_placeholder": "Aloitteen otsikko",
        "init_title_length": "Aloitteen otsikko saa olla max. {length} merkkiä pitkä.",
        "init_desc": "Kirjoita seuraavaksi aloitteen sisältö! (max. {length} merkkiä)",
        "init_desc_placeholder": "Aloitteen kuvaus",
        "init_desc_length": "Aloitteen kuvaus saa olla max. {length} merkkiä pitkä.",
        "init_checkup": "Melkein valmista! Tarkista vielä, että aloite näyttää hyvältä:\n\n<b>{title}</b>\n\n{desc}\n\nMuista, että aloitteet tarkastetaan ennen julkaisua.",
        "init_send": "Valmis, lähetä!",
        "init_edit_title": "Muokkaa otsikkoa",
        "init_edit_desc": "Muokkaa kuvausta",
        "init_editing_title": "Kirjoita aloitteelle uusi otsikko: (max. {length} merkkiä)",
        "init_editing_desc": "Kirjoita aloitteelle uusi kuvaus: (max. {length} merkkiä)",
        "init_cancel": "Poista aloite",
        "init_sent": "Kiitos kansalaisaloitteestasi! Aloite on nyt tarkastettavana \u2013 saat tiedon kun se on käsitelty.",
        "init_canceled": "Kansalaisaloitteen luonti lopetettu.",
        "init_published": "Kansalaisaloitteesi <b>{title}</b> on julkaistu! Jos riittävän monta kansalaista kannattaa aloitettasi, se tulee äänestykseen.",
        "init_unconstitutional": "Kansalaisaloitteesi <b>{title}</b> todettiin perustuslain vastaiseksi (= hyvä idea sinänsä, mutta ei toteutettavissa). Voit luoda uuden kansalaisaloitteen, mutta älä lähetä samaa asiaa uudestaan ilman muutoksia.",
        "init_shitpost": "Kansalaisaloitteesi <b>{title}</b> todettiin paskapostaukseksi. {ban}",
        "init_in_review": "Edellinen kansalaisaloitteesi on vielä tarkastuksessa.",
        "init_banned": "Kansalaisaloitteiden luonti on estetty sinulta {mins} minuutin ajaksi.",
        "init_notif": "Uusi kansalaisaloite käyttäjältä {user}!\n\n<b>{title}</b>\n\n{desc}",
        "init_second": "Kannata aloitetta",
        "init_seconded": "Kannatit tätä aloitetta.",
        "init_broken": "Luo uusi aloite komennolla /initiative.",
    },
    "en": {
        "welcome": "You should not see this",
        "choose_lang": "You should not see this",
        "enter_code": "Enter the code you received when arriving at the sitsit.",
        "lang_set": "Language set!\n\nVoit vaihtaa kieltä komennolla /language.",
        "invalid_code": "The code you entered is invalid. Please check for typos and ask for help from sitsit staff if necessary.",
        "code_placeholder": "Code",
        "used_code": "The code you entered has already been used for another Telegram user! Please ask for help from sitsit staff to transfer the code to your account.",
        "area": "Your seat is in <b>table {area}</b>. You may freely choose a seat within the table.",
        "help": "Welcome! {area}\n\nDuring the sitsit, polls will be sent to you via this Telegram bot. You can check the current poll with the command /current.\n\nDuring the sitsit, you can create citizen's initiatives with the command /initiative. You can read existing initiatives with the command /initiatives.\n\n{initnotif}\n\nIf you leave the sitsit, send the command /absent.",
        "absent": "You have been marked as absent. Send any command to return to the sitsit.",
        "unabsent": "You are no longer absent.",
        "poll_confirm": "{title}\n\nAre you sure you want to vote for {option}?",
        "election_confirm": "{title}\n\nAre you sure you want to vote for {option}?",
        "poll_voted": "{title}\n\nYou voted for {option}.",
        "election_voted": "{title}\n\nYou voted for {option}.",
        "init_notifs_on": "You are currently receiving notifications for all new initiatives. You can turn them off with the command /inotifications.",
        "init_notifs_off": "You are currently <b>not</b> receiving notifications for new initiatives. You can turn them back on with the command /inotifications.",
        "init_title": "Welcome to citizen's initiative creation!\n\nThink twice what you want to propose \u2013 initiatives will be checked before publication, and shitposting will lead to a ban from creating initiatives.\n\nFirst, come up with a catchy title for your initiative! (max. {length} characters)",
        "init_title_placeholder": "Initiative title",
        "init_title_length": "The initiative's title must be max. {length} characters.",
        "init_desc": "Now write the content of the initiative! (max. {length} characters)",
        "init_desc_placeholder": "Initiative description",
        "init_desc_length": "The initiative's description must be max. {length} characters.",
        "init_checkup": "Almost done! Now just check that the initiative looks good:\n\n<b>{title}</b>\n\n{desc}\n\nRemember that initiatives will be checked before publication.",
        "init_send": "Done, send it!",
        "init_edit_title": "Edit title",
        "init_edit_desc": "Edit description",
        "init_editing_title": "Write a new title for the initiative: (max. {length} characters)",
        "init_editing_desc": "Write a new description for the initiative: (max. {length} characters)",
        "init_cancel": "Delete initiative",
        "init_sent": "Thank you for your initiative! It's now in review \u2013 we'll inform you when it's ready.",
        "init_canceled": "Initiative creation cancelled.",
        "init_published": "Your initiative <b>{title}</b> was just published! If enough people second it, it will be voted on.",
        "init_unconstitutional": "Your initiative <b>{title}</b> was deemed unconstitutional (= good idea, but won't/can't be implemented). You may create another one, but don't repeat the same thing without changes.",
        "init_shitpost": "Your initiative <b>{title}</b> was deemed to be a shitpost. {ban}",
        "init_in_review": "Your previous initiative is still in review.",
        "init_banned": "You have been banned from creating initiatives for {mins} minutes.",
        "init_notif": "New citizen's initiative by {user}!\n\n<b>{title}</b>\n\n{desc}",
        "init_second": "Second initiative",
        "init_seconded": "You have seconded this initiative.",
        "init_broken": "Create a new initiative with the command /initiative.",
    },
}


def loc(context: AppContext):
    if context.user_data.lang is None:
        raise RuntimeError("no lang set for user")
    return locale[context.user_data.lang]

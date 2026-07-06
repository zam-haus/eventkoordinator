# E-Mail-Übersicht: Eventkoordinator

| Template | Zusammenfassung | Enthaltene Informationen | Zweck der Mail | Empfänger |
|---|---|---|---|---|
| **`submit`** | Eingangsbestätigung für eine Einreichung | Titel der Einreichung, Call-Titel, Link zur Einreichung | Bestätigung, dass die Einreichung erfolgreich eingegangen ist | Einreicher (Owner) |
| **`submit_contact`** | Benachrichtigung über neue/überarbeitete Einreichung | Titel, Name des Einreichers, Call-Titel, Link zur Einreichung | Verantwortlichen über eine neue oder überarbeitete Einreichung informieren | Verantwortlicher des Calls |
| **`accept`** | Einreichung wurde angenommen | Titel der Einreichung, Call-Titel, Link zur Einreichung | Einreicher über die Annahme seiner Einreichung informieren | Einreicher (Owner) |
| **`reject`** | Einreichung wurde abgelehnt | Titel der Einreichung, Call-Titel, Link zur Einreichung | Einreicher über die Ablehnung seiner Einreichung informieren | Einreicher (Owner) |
| **`revise`** | Überarbeitung der Einreichung erbeten | Titel, Call-Titel, Gutachterkommentare oder Team-Kommentar, Link zur Einreichung | Einreicher auffordern, seine Einreichung zu überarbeiten, mit konkreten Hinweisen | Einreicher (Owner) |
| **`review_requested`** | Bitte um Begutachtung einer Einreichung | Name des Gutachters, Titel der Einreichung, Call-Titel, Link zur Einreichung | Gutachter über eine ihm zugewiesene Einreichung informieren und zur Bewertung auffordern | Gutachter (Reviewer) |
| **`review_given`** | Gutachten wurde abgegeben | Gutachtername, Ergebnis (Angenommen/Abgelehnt/Überarbeitung), Kommentar, Link zur Einreichung | Verantwortlichen über ein eingegangenes Gutachten informieren | Verantwortlicher des Calls |
| **`event_submit_owner`** | Neuer Terminvorschlag für die Einreichung des Owners | Veranstaltungsname, Beginn, Ende, Call-Titel, Einreichungstitel, Link zum Terminvorschlag | Einreicher über einen neuen Terminvorschlag informieren und zur Zu- oder Absage auffordern | Einreicher (Owner) |
| **`event_confirm_owner`** | Termin wurde bestätigt (Nachricht an Owner) | Veranstaltungsname, Beginn, Ende, Call-Titel, Einreichungstitel, Link zum Termin | Einreicher über den endgültig bestätigten Termin informieren | Einreicher (Owner) |
| **`event_confirm_contact`** | Termin wurde bestätigt (Nachricht an Kontakt) | Veranstaltungsname, Einreichungstitel, Einreichername, Beginn, Ende, Links zu Einreichung und Termin | Verantwortlichen darüber informieren, dass ein Termin nun beidseitig bestätigt ist | Verantwortlicher des Calls |
| **`event_approve_contact`** | Owner hat Terminvorschlag aktiv bestätigt | Veranstaltungsname, Einreichungstitel, Einreichername, Links zu Einreichung und Terminvorschlag | Verantwortlichen informieren, dass der Einreicher einem Terminvorschlag zugestimmt hat | Verantwortlicher des Calls |
| **`event_reject_contact`** | Owner hat Terminvorschlag abgelehnt | Veranstaltungsname, Einreichungstitel, Einreichername, Links zu Einreichung und Terminvorschlag | Verantwortlichen informieren, dass der Einreicher einen Terminvorschlag abgelehnt hat | Verantwortlicher des Calls |
| **`event_cancel_owner`** | Ein Termin wurde abgesagt (Nachricht an Owner) | Veranstaltungsname, Beginn, Ende, Call-Titel, Einreichungstitel, Link zum Termin | Einreicher über die Absage eines bestätigten Termins informieren | Einreicher (Owner) |
| **`event_cancel_contact`** | Ein Termin wurde abgesagt (Nachricht an Kontakt) | Veranstaltungsname, Einreichungstitel, Einreichername, Beginn, Ende, Links zu Einreichung und Termin | Verantwortlichen über die Absage eines bestätigten Termins informieren | Verantwortlicher des Calls |

## Terminologie

- **Owner / Einreicher**: Person, die einen Vorschlag (Proposal) eingereicht hat
- **Verantwortlicher des Calls**: Eine fest konfigurierte E-Mail-Adresse am Call-Objekt (`call.responsible_email`) — kein Systembenutzer, sondern eine Kontaktadresse des Veranstalters, die Benachrichtigungen auf der Organisatorenseite erhält
- **Call**: Ausschreibung, für die Einreichungen eingereicht werden können

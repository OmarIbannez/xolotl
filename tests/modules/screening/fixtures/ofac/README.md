# OFAC SDN list (trimmed)

Retrieved 2026-10-19 from the Sanctions List Service exports, https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/ (listed on https://sanctionslist.ofac.treas.gov/Home/static/sdn.html). Publication `dataAsOf` 2026-10-15; 19,457 SDN entries.

| File | Source | Full size | Full SHA-256 |
|---|---|---|---|
| `sdn_enhanced.xml` | `SDN_ENHANCED.XML` (schema `ENHANCED_XML.xsd`) | 109,874,064 bytes | `8ac78103a24627746d6ff5cf00583ac58940568c2a22a157fc82bfd26a6c0c28` |
| `sdn.csv` | `SDN.CSV` | 5,741,043 bytes | `0dee0d2de5e5a240e3e545741ee2903cfa3508666d2f2841d5d1ff2d59f0ce71` |
| `alt.csv` | `ALT.CSV` | 1,063,973 bytes | `7b76ad83a434083be9525f5effda986b1c1a70afd028836cf4cae031a13fb365` |

Trimmed to the same 30 entries (uids) in all three files, copied verbatim, so the XML and CSV parsers can be compared entry by entry. For the XML, `publicationInfo` is kept whole, `referenceValues` is cut to the values those 30 entities reference, and `featureTypes` (not read by the parser) is dropped. The CSV keeps OFAC's CRLF line endings and the trailing DOS end-of-file byte of `SDN.CSV`. The tests zip `sdn.csv` and `alt.csv` in memory, as the CSV fallback expects.

Entries were picked as the smallest that cover: Mexican people and companies with C.U.R.P. and R.F.C., birth-date ranges and day, month and year precision, Cyrillic, Arabic and Chinese translations, low-quality aliases, a vessel with an IMO number, an aircraft, Bitcoin (XBT) and Ether (ETH) addresses, citizenship and "Nationality of Registration" countries, a Palestinian nationality, passports and national IDs with issuing countries, and organizations with aliases.

Personal data: only what OFAC itself publishes in this list.

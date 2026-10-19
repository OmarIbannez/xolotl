# Crypto travel rule — explainer
Source: https://app.notion.com/3fde5630e5e3818fb517ec69c9a9717a

<callout icon="⚠️" color="yellow_bg">
	**Corrections from later research:** Res. BCB 588/2026 was revoked before taking effect and replaced by **Res. BCB 591/2026** (automatic COAF reporting of self-custody crypto transfers and foreign-cash FX ≥ US\$10k). Peru's travel rule comes from **Res. SBS 02648-2024** (in force 01-08-2026), not "000132-2024". Brazil's travel-rule phases: domestic 02-02-2027, international 02-02-2028. See <mention-page url="https://app.notion.com/p/3fde5630e5e381fda6a5de38d932d48d"/> §0.
</callout>
**Date:** 2026-10-17
**Status:** Research note. LATAM dates come from vendor and law-firm sources and must be verified against official texts.
**Related:** <mention-page url="https://app.notion.com/p/3fde5630e5e381a7a13fd6fda3ef15ad"/>
---
## Origin
- Banks sending **wire transfers** have long had to include who is sending and who is receiving. In the US this has applied to transfers of US\$3,000 or more since 1996 (Bank Secrecy Act). It is called the "travel rule" because the identity data *travels with* the payment.
- In **2019 the FATF**, the global standards body for anti-money-laundering, extended the same rule to crypto through **Recommendation 16** and its interpretive note.
- It now applies to virtual asset service providers (**VASPs**): exchanges, custodians, brokers, and some wallet providers.
## What it requires
When a customer sends crypto from one VASP to another, usually above roughly **USD/EUR 1,000** (some jurisdictions set no minimum, e.g. the EU):
<table header-row="true">
<tr>
<td>Side</td>
<td>Obligation</td>
</tr>
<tr>
<td>**Sending VASP (originator)**</td>
<td>Collect and transmit the sender's name, account or wallet, and an identifier (national ID, address, or date and place of birth), plus the recipient's name and wallet.</td>
</tr>
<tr>
<td>**Receiving VASP (beneficiary)**</td>
<td>Receive the data, check it against its own customer, screen both parties (sanctions, PEP), keep records, and handle missing or incorrect data (reject, return, or file a suspicious-activity report).</td>
</tr>
</table>
## Why it is technically hard
1. **The blockchain has no field for identity data**, so it has to travel through a separate channel.
	- Messaging protocols between VASPs include **TRP** (Travel Rule Protocol), **TRISA**, the **Notabene** network, Sumsub, and Veriscope.
	- **IVMS101** is the common data format for originator and beneficiary details.
2. **Finding the counterparty.** You have to work out whether the destination address belongs to a VASP, and which one. This uses blockchain analytics and VASP directories.
3. **Wallets a customer controls directly (unhosted or self-custodied).** There is no counterparty VASP to send data to. Rules usually ask for **proof of ownership**: signing a message, sending a small test amount (the "Satoshi test"), or a screenshot or attestation, plus wallet risk screening.
4. **The "sunrise problem".** Countries adopt the rule at different times, so the counterparty VASP may not be required to send or receive the data, or may not be ready to.
5. **Interoperability.** Different protocols don't always talk to each other, so most VASPs use an aggregator.
## LATAM status
<table header-row="true">
<tr>
<td>Country</td>
<td>Rule</td>
<td>Timing</td>
<td>Confidence</td>
</tr>
<tr>
<td>**Brazil**</td>
<td>Res. BCB 519/520/521 (Nov 2025) under Lei 14.478/2022. Res. 520 covers AML duties and the travel rule</td>
<td>Phased in: domestic \~Feb 2027, international \~Feb 2028. Sources disagree on the start dates</td>
<td>⚠️ Verify</td>
</tr>
<tr>
<td>**Brazil**</td>
<td>Res. BCB 588 (Sep 2026)</td>
<td>From 1 Oct 2026, automatic COAF reporting of transfers to and from **self-custodied wallets**</td>
<td>⚠️ Single source</td>
</tr>
<tr>
<td>**Argentina**</td>
<td>UIF Res. 49/2024 (travel rule) + CNV RG 1058 (PSAV regime, effective 31 Dec 2025)</td>
<td>In force; technical guidance reportedly incomplete</td>
<td>⚠️ Verify</td>
</tr>
<tr>
<td>**Peru**</td>
<td>Res. SBS 000132-2024</td>
<td>Enforcement \~Aug 2026</td>
<td>⚠️ Verify</td>
</tr>
<tr>
<td>**Mexico**</td>
<td>No dedicated crypto statute found</td>
<td>Unclear</td>
<td>—</td>
</tr>
</table>
## Implications for the platform
- **Relevant only if** clients include VASPs or fintechs offering crypto. For banks, traditional fintechs, and Mexican LFPIORPI businesses it is out of scope for the MVP.
- **Build vs. buy:** integrate a travel-rule provider (Notabene, Sumsub, 21analytics, etc.) rather than build the protocol layer. The platform's value is in connecting it to:
	- customer data (to fill IVMS101 originator and beneficiary fields)
	- screening of the counterparty name and wallet address
	- case management (missing or mismatched data → alert → case → possible suspicious-activity report)
	- proof-of-ownership records for self-custodied wallets, stored as `verification_check` rows
	- Brazil's automatic COAF reporting for self-custodied wallet transfers
- **Priority:** P1 overall, P0 for VASP clients.
## Sources
- [Notabene – BCB VASP resolutions](https://notabene.id/post/brazils-central-bank-regulates-virtual-asset-service-providers-what-bcb-resolutions-mean-for-crypto-compliance)
- [21analytics – Brazil Travel Rule](https://21analytics.co/travel-rule-regulations/brazil-travel-rule-regulation)
- [Sumsub – Brazil travel rule era](https://sumsub.com/media/spotlight/brazil-travel-rule-era-compliance-for-vasps/)
- [Vixio – Argentina and Brazil VASP compliance](https://www.vixio.com/insights/pc-argentina-and-brazil-set-stage-vasp-compliance)
- [Sumsub – Crypto in LATAM](https://sumsub.com/blog/crypto-in-latam)
- [Schiefler – COAF (Res. BCB 588)](https://schiefler.adv.br/assunto/coaf/)

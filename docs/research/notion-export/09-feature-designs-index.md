# Feature designs — index
Source: https://app.notion.com/3fde5630e5e381a1b4a4cd165a3aea66

**Date:** 2026-10-17
Each document starts with an **executive summary**, then covers requirements (with country sources), architecture, **data model (SQL)**, workflows and state machines, algorithms, APIs and events, integrations, non-functional targets, testing and a build plan.
<table header-row="true">
<tr>
<td>#</td>
<td>Feature</td>
<td>Doc</td>
<td>v1 effort (eng-weeks)</td>
</tr>
<tr>
<td>00</td>
<td>Platform foundations (tenancy, encryption, policy packs, units, calendar, events, API, connector SDK)</td>
<td><mention-page url="https://app.notion.com/p/3fde5630e5e3811db68beb230d57a476"/></td>
<td>≈ 8</td>
</tr>
<tr>
<td>01</td>
<td>Customer onboarding (KYC)</td>
<td><mention-page url="https://app.notion.com/p/3fde5630e5e3813385c3c590786261ed"/></td>
<td>≈ 14</td>
</tr>
<tr>
<td>02</td>
<td>Business onboarding (KYB & beneficial ownership)</td>
<td><mention-page url="https://app.notion.com/p/3fde5630e5e381afbeb3c7f4bf0fdbf5"/></td>
<td>≈ 10</td>
</tr>
<tr>
<td>03</td>
<td>Screening (sanctions, PEP, adverse media, freezing)</td>
<td><mention-page url="https://app.notion.com/p/3fde5630e5e3814da2f5ce4e03addb79"/></td>
<td>≈ 12</td>
</tr>
<tr>
<td>04</td>
<td>Risk scoring + enterprise-wide risk assessment</td>
<td><mention-page url="https://app.notion.com/p/3fde5630e5e38138a2dfc3a914546e81"/></td>
<td>≈ 10</td>
</tr>
<tr>
<td>05</td>
<td>Transaction monitoring</td>
<td><mention-page url="https://app.notion.com/p/3fde5630e5e3817b9987f2a49b2bd540"/></td>
<td>≈ 14</td>
</tr>
<tr>
<td>06</td>
<td>Investigations & case management</td>
<td><mention-page url="https://app.notion.com/p/3fde5630e5e3814783fbe56ba6027f61"/></td>
<td>≈ 10</td>
</tr>
<tr>
<td>07</td>
<td>Regulatory reporting engine</td>
<td><mention-page url="https://app.notion.com/p/3fde5630e5e381eaba30f2670b688bf9"/></td>
<td>≈ 26.5 (MX launch ≈ 12.5)</td>
</tr>
<tr>
<td>08</td>
<td>Compliance calendar & governance</td>
<td><mention-page url="https://app.notion.com/p/3fde5630e5e381f5b59bdd8bfb681a96"/></td>
<td>≈ 5</td>
</tr>
</table>
**Total v1 engineering** ≈ 110 engineer-weeks across all features (≈ 85 for the Mexico launch subset: foundations 8, MX onboarding \~11, MX KYB \~8, screening 12, customer risk 6, monitoring 14, cases 10, MX reports 12.5, calendar 5). With a team of 6–8 engineers and realistic parallelism, that is roughly a **4–5 month** build to an MX launch, plus vendor contracting and design-partner validation running in parallel.
**Supporting research:**
- <mention-page url="https://app.notion.com/p/3fde5630e5e381989aacdee9e75c45db"/>
- <mention-page url="https://app.notion.com/p/3fde5630e5e38169a3e0eb3c3f23c573"/>
- Regulatory reporting research per country: <mention-page url="https://app.notion.com/p/3fde5630e5e381ad879bdf8c29dfbf98"/> · <mention-page url="https://app.notion.com/p/3fde5630e5e38109bb2fd118304e5427"/> · <mention-page url="https://app.notion.com/p/3fde5630e5e38194810ffb516e55f6fe"/>
**Related:**
- Product: <mention-page url="https://app.notion.com/p/3fde5630e5e38175b09bf9e362c690e5"/> · <mention-page url="https://app.notion.com/p/3fde5630e5e381fda6a5de38d932d48d"/>
- Architecture: <mention-page url="https://app.notion.com/p/3fde5630e5e381b3bf91f5271e50b24a"/> · <mention-page url="https://app.notion.com/p/3fde5630e5e38175aa91cf38caaaf97e"/> · <mention-page url="https://app.notion.com/p/3fde5630e5e38162885fd93c9ea7118b"/> · <mention-page url="https://app.notion.com/p/3fde5630e5e3815191c9ddd6987d3598"/>

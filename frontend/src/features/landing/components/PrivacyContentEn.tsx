import ExternalLink from './ExternalLink';

// Approved legal copy (UtilidadesDelProgramador/despliegue-app/verificacion-oauth/
// BORRADOR-privacy-policy.md, English version). Content changes must go through
// a new approved draft — do not reword ad hoc.
export default function PrivacyContentEn() {
  return (
    <>
      <h1>Privacy Policy</h1>
      <p>
        <strong>Last updated: July 15, 2026</strong>
      </p>
      <p>
        Missela ("we", "us") is an email client that lets you read and manage your Gmail and Outlook
        accounts from a single inbox. It is operated by Andrés Muelas Tenorio, an individual
        software developer based in Spain. You can reach us at{' '}
        <a href="mailto:support@missela.app">support@missela.app</a>.
      </p>
      <p>
        This policy explains, in plain language, what data Missela accesses when you connect your
        email accounts, what we store on our servers, what we use it for, and how you can delete it.
        It applies to the web application at missela.app.
      </p>
      <p>
        The short version: we access your mailboxes only to show them to you and let you act on
        them. We don't sell data, we don't show ads, we don't train AI models on your email, and no
        human reads your messages.
      </p>

      <h2>1. Data we access and why</h2>
      <p>
        <strong>Your Missela account.</strong> When you sign in with Google or Microsoft, we receive
        your basic profile from the identity token: a user identifier, your email address, your name
        and your profile picture. We use it to create your Missela account and show who is signed
        in. We never see your password.
      </p>
      <p>
        <strong>Connected mailboxes.</strong> When you connect a Gmail account, Missela requests the
        following OAuth scopes:
      </p>
      <ul>
        <li>
          <code>gmail.modify</code> - read, compose, send and manage labels on your messages. This
          is the minimum Gmail scope that supports a full email client (reading mail, marking
          read/unread, starring, moving to spam/trash/archive, managing drafts). It deliberately
          does NOT allow permanently deleting messages.
        </li>
        <li>
          <code>openid</code>, <code>email</code>, <code>profile</code> - to identify the connected
          account.
        </li>
      </ul>
      <p>
        When you connect an Outlook account, Missela requests the Microsoft Graph delegated
        permissions <code>Mail.ReadWrite</code>, <code>Mail.Send</code>, <code>offline_access</code>{' '}
        and basic profile scopes, which cover the same functionality.
      </p>
      <p>
        You can revoke this access at any time from your provider's security settings (Google:{' '}
        <ExternalLink href="https://myaccount.google.com/permissions">
          myaccount.google.com/permissions
        </ExternalLink>{' '}
        · Microsoft:{' '}
        <ExternalLink href="https://account.live.com/consent/Manage">
          account.live.com/consent/Manage
        </ExternalLink>
        ), as well as by disconnecting the account inside Missela.
      </p>

      <h2>2. What we store on our servers</h2>
      <p>
        Missela keeps a working copy of parts of your mailbox so the app is fast and searchable.
        Concretely:
      </p>
      <ul>
        <li>
          <strong>OAuth tokens</strong> for each connected account, encrypted at rest. They are the
          credentials that let Missela talk to Gmail/Outlook on your behalf.
        </li>
        <li>
          <strong>Message metadata</strong>: subject, sender, primary recipient, date, read/starred
          state, folder and conversation id. This is what powers your inbox list and search.
        </li>
        <li>
          <strong>Message content</strong>: the sanitized HTML body of messages you open (plus a
          small prefetch of recent unread ones). Cached copies expire after 7 days without being
          opened.
        </li>
        <li>
          <strong>Attachments</strong>: files you download or preview are cached for up to 30 days
          after last access. Attachment names and sizes are kept as metadata.
        </li>
        <li>
          <strong>Drafts</strong> you compose in Missela, including their attachments, synchronized
          with your provider.
        </li>
        <li>
          <strong>Your settings</strong>: connected account list, per-account signature, virtual
          mailboxes (saved views) and mailbox names.
        </li>
        <li>
          <strong>Remote images</strong> referenced inside emails are fetched through our image
          proxy and cached by URL. This is a privacy feature: the sender's server sees our server,
          not your IP address, browser or location. This cache stores only the image files
          themselves, keyed by URL, and is not linked to your mailbox.
        </li>
      </ul>
      <p>
        Some preferences (interface language, last-sync timestamps) live only in your browser's
        local storage and never reach our servers.
      </p>
      <p>
        The authoritative copy of your email always remains at your provider. Missela is a window
        onto your Gmail/Outlook account, not a replacement archive: deleting data from Missela never
        deletes anything from your real mailbox.
      </p>

      <h2>3. What we use your data for</h2>
      <p>
        One thing: providing the features you see in the interface. Reading and searching your mail,
        sending and replying, managing drafts and attachments, favorites, spam/trash/archive,
        signatures, unified and virtual mailboxes.
      </p>
      <p>We do not:</p>
      <ul>
        <li>sell, rent or transfer your data to third parties;</li>
        <li>use your email for advertising of any kind;</li>
        <li>use your email to train AI or machine-learning models;</li>
        <li>create profiles about you or track you across the web.</li>
      </ul>
      <p>There are no third-party analytics or tracking scripts on missela.app.</p>

      <h2>4. Google user data - Limited Use disclosure</h2>
      <p>
        Missela's use and transfer to any other app of information received from Google APIs will
        adhere to the{' '}
        <ExternalLink href="https://developers.google.com/terms/api-services-user-data-policy">
          Google API Services User Data Policy
        </ExternalLink>
        , including the Limited Use requirements.
      </p>
      <p>
        In particular: Google user data is used only to provide the user-facing features described
        above, is never sold, is never used for advertising, and is never used to train generalized
        AI or machine-learning models. Human access to this data is limited to the cases listed in
        section 5.
      </p>

      <h2>5. Who can see your data</h2>
      <p>
        Nobody, in normal operation. Your messages are processed automatically by our software. A
        human (the operator) may access specific data only when:
      </p>
      <ul>
        <li>you explicitly ask for it and consent, for example to debug a support issue;</li>
        <li>it is necessary for security purposes, such as investigating abuse;</li>
        <li>we are required to by applicable law;</li>
        <li>the data is aggregated and anonymized for internal operational statistics.</li>
      </ul>
      <p>
        We share data with no third parties, with two narrow exceptions: our hosting provider,
        Hetzner Online GmbH (servers located in Nuremberg, Germany, EU), which processes it on our
        behalf as infrastructure, and your own email providers (Google, Microsoft), which receive
        the API calls needed to operate on your mailbox. We would also disclose data if legally
        compelled to.
      </p>

      <h2>6. Security</h2>
      <ul>
        <li>
          All traffic between your browser, our servers and your providers uses TLS (HTTPS
          everywhere; the .app domain enforces it).
        </li>
        <li>
          OAuth tokens are encrypted at rest with symmetric encryption (Fernet); the key never
          leaves the server.
        </li>
        <li>
          The service runs on hardened containers with a network firewall, rate limiting and
          least-privilege configuration, hosted in the EU.
        </li>
        <li>
          Cached message content is isolated per account and removed automatically by the expiry
          rules above.
        </li>
      </ul>
      <p>
        No system is perfectly secure, but the surface is deliberately small: one server, no
        third-party data processors beyond hosting, and short-lived caches.
      </p>

      <h2>7. Data retention and deletion</h2>
      <ul>
        <li>
          Cached message bodies expire 7 days after you last open them. Cached attachment files are
          kept for up to 30 days after last access.
        </li>
        <li>
          Everything else tied to a connected account (metadata, drafts, tokens, signature) is kept
          while the account stays connected.
        </li>
        <li>
          <strong>Disconnecting an account</strong> inside Missela immediately deletes all its
          stored data: tokens, metadata, cached content, attachments and drafts.
        </li>
        <li>
          <strong>Deleting your Missela account</strong> (Settings → Account) immediately deletes
          your user record and everything under it, for all connected accounts.
        </li>
        <li>
          You can also revoke Missela's access from Google/Microsoft at any time (links in section
          1); with access revoked, Missela can no longer read anything from your mailbox.
        </li>
      </ul>

      <h2>8. Your rights (GDPR)</h2>
      <p>
        If you are in the European Economic Area, the data controller is Andrés Muelas Tenorio
        (contact: <a href="mailto:support@missela.app">support@missela.app</a>). Processing is based
        on: performance of the service you request (Art. 6(1)(b) GDPR) and your consent expressed
        through the OAuth grant (Art. 6(1)(a)), which you can withdraw at any time as described
        above.
      </p>
      <p>
        You have the right to access, rectify, delete, restrict or object to the processing of your
        personal data, and to data portability. Most of these you can exercise directly in the app
        (viewing your mail is access; disconnecting/deleting is erasure). For anything else, write
        to <a href="mailto:support@missela.app">support@missela.app</a>. You also have the right to
        lodge a complaint with your supervisory authority; in Spain, the Agencia Española de
        Protección de Datos (<ExternalLink href="https://www.aepd.es">www.aepd.es</ExternalLink>).
      </p>

      <h2>9. Cookies</h2>
      <p>
        Missela uses a single first-party session cookie, strictly necessary to keep you signed in
        (httpOnly, Secure). No advertising cookies, no third-party cookies, no cross-site tracking.
        Because only essential cookies are used, no cookie consent banner is required.
      </p>

      <h2>10. Children</h2>
      <p>
        Missela is not directed at children under 16, and we do not knowingly process their data.
      </p>

      <h2>11. Changes to this policy</h2>
      <p>
        We will post any changes on this page and update the date at the top. If a change
        meaningfully affects how your data is handled, we will announce it inside the app before it
        takes effect.
      </p>

      <h2>12. Contact</h2>
      <p>
        <a href="mailto:support@missela.app">support@missela.app</a>
      </p>
    </>
  );
}

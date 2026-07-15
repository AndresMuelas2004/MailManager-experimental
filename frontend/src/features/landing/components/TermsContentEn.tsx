import { Link } from 'react-router-dom';

// Approved legal copy (UtilidadesDelProgramador/despliegue-app/verificacion-oauth/
// BORRADOR-terms-of-service.md, English version). Content changes must go through
// a new approved draft — do not reword ad hoc.
export default function TermsContentEn() {
  return (
    <>
      <h1>Terms of Service</h1>
      <p>
        <strong>Last updated: July 15, 2026</strong>
      </p>
      <p>
        These terms govern your use of Missela, the email client available at missela.app, operated
        by Andrés Muelas Tenorio, an individual developer based in Spain ("we", "us"). By creating
        an account or using the service you agree to them.
      </p>

      <h2>1. What Missela is</h2>
      <p>
        Missela lets you connect your Gmail and Outlook accounts and read, search, organize, compose
        and send email from a single interface. Your email itself stays at your provider (Google or
        Microsoft); Missela operates on it through the official APIs, with the permissions you grant
        during sign-in.
      </p>
      <p>
        Missela is currently in <strong>early access (beta)</strong> and is free of charge while in
        beta. Features may change, and occasional bugs or downtime are to be expected at this stage.
      </p>

      <h2>2. Your account</h2>
      <p>
        You need to sign in with a Google or Microsoft account. You are responsible for the security
        of that account. You must be at least 16 years old to use Missela. Technical limits may
        apply to the service (for example, a maximum number of connected mailboxes per user).
      </p>

      <h2>3. Acceptable use</h2>
      <p>
        Don't use Missela to send spam, phishing or malware, to harass anyone, to break any law, or
        to interfere with the service itself (probing, overloading, automated scraping, attempting
        to access other users' data). We may suspend or terminate accounts that do.
      </p>

      <h2>4. Your data</h2>
      <p>
        Your email belongs to you. We process it only to provide the service, as described in the{' '}
        <Link to="/privacy">Privacy Policy</Link>, which is part of these terms. Disconnecting an
        account or deleting your Missela account removes the associated data from our servers, as
        described there.
      </p>

      <h2>5. Third-party services</h2>
      <p>
        Missela depends on Google and Microsoft APIs. Your use of Gmail and Outlook remains governed
        by their own terms and policies. We are not responsible for those services, for changes to
        their APIs, or for actions they take on your accounts (such as rate limits or security
        blocks).
      </p>

      <h2>6. Availability and warranty disclaimer</h2>
      <p>
        Missela is provided "as is" and "as available", without warranties of any kind, to the
        maximum extent permitted by law. We do not guarantee uninterrupted service, error-free
        operation, or that cached data will be preserved. Missela is not an email archive: the
        authoritative copy of your mail always lives at your provider, so a failure of Missela never
        means losing your email.
      </p>

      <h2>7. Limitation of liability</h2>
      <p>
        To the maximum extent permitted by law, we will not be liable for indirect, incidental or
        consequential damages, loss of profits or loss of data arising from the use of the service.
        Nothing in these terms limits liability that cannot be limited by law, including liability
        arising from willful misconduct or gross negligence, or your statutory rights as a consumer.
      </p>

      <h2>8. Termination</h2>
      <p>
        You can stop using Missela and delete your account at any time from Settings. We may suspend
        or terminate accounts that violate these terms, and we may discontinue the beta with
        reasonable prior notice inside the app.
      </p>

      <h2>9. Changes to these terms</h2>
      <p>
        We may update these terms as the service evolves. If a change is material we will announce
        it inside the app before it takes effect. Using the service after that means you accept the
        updated terms.
      </p>

      <h2>10. Governing law</h2>
      <p>
        These terms are governed by Spanish law. If you use Missela as a consumer in the EU, you
        keep any mandatory protections and jurisdiction rules of your country of residence.
      </p>

      <h2>11. Contact</h2>
      <p>
        <a href="mailto:support@missela.app">support@missela.app</a>
      </p>
    </>
  );
}

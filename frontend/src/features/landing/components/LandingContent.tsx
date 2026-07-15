import type { ComponentType, ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { Filter, Inbox, Mail, Search, Send, ShieldCheck } from 'lucide-react';

import type { Lang } from '../../../lib/i18n';

type Props = {
  lang: Lang;
};

// Inline operator / scope chip on the light feature cards.
function Op({ children }: { children: ReactNode }) {
  return (
    <code className="rounded bg-zinc-100 px-1 py-0.5 font-mono text-[13px] text-zinc-800">
      {children}
    </code>
  );
}

// Same chip, tuned for the dark "Private by design" block.
function OpDark({ children }: { children: ReactNode }) {
  return (
    <code className="rounded bg-white/15 px-1.5 py-0.5 font-mono text-[13px] text-white">
      {children}
    </code>
  );
}

type LandingCopy = {
  hero: { title: string; subtitle: string; cta: string; betaNote: string };
  featuresHeading: string;
  features: Array<{ icon: ComponentType<{ className?: string }>; title: string; body: ReactNode }>;
  howHeading: string;
  steps: Array<{ lead: string; rest: string }>;
  privacy: { heading: string; p1: ReactNode; p2: string; moreLead: string; moreLabel: string };
  finalCta: { title: string; subtitle: string; button: string };
};

const COPY: Record<Lang, LandingCopy> = {
  en: {
    hero: {
      title: 'All your email. One inbox.',
      subtitle:
        'Missela brings your Gmail and Outlook accounts together in a single place. Read, search, reply and organize everything without jumping between tabs.',
      cta: 'Get started',
      betaNote: 'Free while in beta.',
    },
    featuresHeading: 'Features',
    features: [
      {
        icon: Inbox,
        title: 'One inbox for everything',
        body: 'Every account in a single list, or one at a time when you need focus. Unified views for sent, spam, trash, archive and favorites too.',
      },
      {
        icon: Mail,
        title: 'Gmail and Outlook, together',
        body: 'Connect up to 15 accounts, personal or work, from both providers. Each one keeps its own identity: signatures, drafts and folders stay per-account.',
      },
      {
        icon: Search,
        title: 'Search that speaks your language',
        body: (
          <>
            Operators like <Op>from:</Op>, <Op>to:</Op>, <Op>subject:</Op>, <Op>has:attachment</Op>,{' '}
            <Op>before:</Op>/<Op>after:</Op> or <Op>is:unread</Op>, across all your accounts at
            once. Accent-insensitive, the way search should be.
          </>
        ),
      },
      {
        icon: Send,
        title: 'A full email client',
        body: 'Compose, reply, reply-all and forward with rich text, attachments up to 25 MB and per-account signatures. Drafts sync with your provider, so nothing gets stranded in Missela.',
      },
      {
        icon: Filter,
        title: 'Your own views',
        body: 'Virtual mailboxes are saved views you define once: pick accounts, add filters (unread, favorites, sender, subject...), give it a name. Your inbox, your rules.',
      },
      {
        icon: ShieldCheck,
        title: 'Private by design',
        body: "Remote images load through our proxy, so senders can't see your IP or track when you open their emails. OAuth tokens are encrypted at rest. Hosted in the EU. No ads, no tracking, no data selling. Ever.",
      },
    ],
    howHeading: 'How it works',
    steps: [
      { lead: 'Sign in', rest: 'with your Google or Microsoft account.' },
      { lead: 'Connect your mailboxes', rest: '- as many as you need.' },
      { lead: "That's it.", rest: 'Read, search and send from one place.' },
    ],
    privacy: {
      heading: 'Private by design',
      p1: (
        <>
          Missela asks only for the permissions an email client needs: reading and managing your
          messages (<OpDark>gmail.modify</OpDark> on Google, <OpDark>Mail.ReadWrite</OpDark> +{' '}
          <OpDark>Mail.Send</OpDark> on Microsoft) and your basic profile to sign you in. Your email
          is used for exactly one thing: showing it to you and letting you act on it.
        </>
      ),
      p2: 'No human reads your messages. Nothing is sold or shared. Nothing is used to train AI. Cached data expires automatically, and disconnecting an account wipes everything we stored for it.',
      moreLead: 'The full details, in plain language:',
      moreLabel: 'Privacy Policy',
    },
    finalCta: {
      title: 'Try Missela today.',
      subtitle: 'Free during beta. Two clicks to get started.',
      button: 'Get started',
    },
  },
  es: {
    hero: {
      title: 'Todo tu correo. Una sola bandeja.',
      subtitle:
        'Missela reúne tus cuentas de Gmail y Outlook en un único lugar. Lee, busca, responde y organiza todo sin saltar entre pestañas.',
      cta: 'Empezar',
      betaNote: 'Gratis durante la beta.',
    },
    featuresHeading: 'Características',
    features: [
      {
        icon: Inbox,
        title: 'Una bandeja para todo',
        body: 'Todas tus cuentas en una sola lista, o de una en una cuando necesitas foco. También vistas unificadas de enviados, spam, papelera, archivo y favoritos.',
      },
      {
        icon: Mail,
        title: 'Gmail y Outlook, juntos',
        body: 'Conecta hasta 15 cuentas, personales o de trabajo, de ambos proveedores. Cada una conserva su identidad: firmas, borradores y carpetas van por cuenta.',
      },
      {
        icon: Search,
        title: 'Búsqueda que habla tu idioma',
        body: (
          <>
            Operadores como <Op>from:</Op>, <Op>to:</Op>, <Op>subject:</Op>, <Op>has:attachment</Op>
            , <Op>before:</Op>/<Op>after:</Op> o <Op>is:unread</Op>, en todas tus cuentas a la vez.
            Insensible a acentos, como debe ser.
          </>
        ),
      },
      {
        icon: Send,
        title: 'Un cliente de correo completo',
        body: 'Redacta, responde, responde a todos y reenvía con texto enriquecido, adjuntos de hasta 25 MB y firmas por cuenta. Los borradores se sincronizan con tu proveedor: nada se queda atrapado en Missela.',
      },
      {
        icon: Filter,
        title: 'Tus propias vistas',
        body: 'Las bandejas virtuales son vistas guardadas que defines una vez: eliges cuentas, añades filtros (no leídos, favoritos, remitente, asunto...), les pones nombre. Tu bandeja, tus reglas.',
      },
      {
        icon: ShieldCheck,
        title: 'Privada por diseño',
        body: 'Las imágenes remotas cargan a través de nuestro proxy, así que los remitentes no ven tu IP ni saben cuándo abres sus correos. Los tokens OAuth van cifrados en reposo. Alojada en la UE. Sin publicidad, sin rastreo, sin venta de datos. Nunca.',
      },
    ],
    howHeading: 'Cómo funciona',
    steps: [
      { lead: 'Inicia sesión', rest: 'con tu cuenta de Google o Microsoft.' },
      { lead: 'Conecta tus buzones', rest: '- tantos como necesites.' },
      { lead: 'Ya está.', rest: 'Lee, busca y envía desde un solo sitio.' },
    ],
    privacy: {
      heading: 'Privada por diseño',
      p1: (
        <>
          Missela pide solo los permisos que un cliente de correo necesita: leer y gestionar tus
          mensajes (<OpDark>gmail.modify</OpDark> en Google, <OpDark>Mail.ReadWrite</OpDark> +{' '}
          <OpDark>Mail.Send</OpDark> en Microsoft) y tu perfil básico para iniciar sesión. Tu correo
          se usa exactamente para una cosa: mostrártelo y que puedas actuar sobre él.
        </>
      ),
      p2: 'Ningún humano lee tus mensajes. Nada se vende ni se comparte. Nada se usa para entrenar IA. Los datos cacheados expiran automáticamente, y desconectar una cuenta borra todo lo que guardábamos de ella.',
      moreLead: 'Todos los detalles, en lenguaje llano:',
      moreLabel: 'Política de privacidad',
    },
    finalCta: {
      title: 'Prueba Missela hoy.',
      subtitle: 'Gratis durante la beta. Dos clics para empezar.',
      button: 'Empezar',
    },
  },
};

const CTA_CLASSES =
  'inline-block rounded-xl bg-blue-600 px-8 py-3 text-base font-semibold text-white shadow-sm transition-colors hover:bg-blue-700';

export default function LandingContent({ lang }: Props) {
  const copy = COPY[lang];

  return (
    <main>
      {/* Hero */}
      <section className="mx-auto w-full max-w-6xl px-4 pt-20 pb-16 text-center sm:px-6 sm:pt-28">
        <h1 className="mx-auto max-w-3xl text-4xl font-extrabold tracking-tight text-zinc-900 sm:text-5xl">
          {copy.hero.title}
        </h1>
        <p className="mx-auto mt-6 max-w-2xl text-lg leading-relaxed text-zinc-600">
          {copy.hero.subtitle}
        </p>
        <div className="mt-8 flex flex-col items-center gap-3">
          <Link to="/login" className={CTA_CLASSES}>
            {copy.hero.cta}
          </Link>
          <p className="text-sm text-zinc-500">{copy.hero.betaNote}</p>
        </div>
      </section>

      {/* Features */}
      <section id="features" className="border-t border-zinc-100 bg-zinc-50/60 py-16 sm:py-20">
        <div className="mx-auto w-full max-w-6xl px-4 sm:px-6">
          <h2 className="text-center text-3xl font-bold tracking-tight text-zinc-900">
            {copy.featuresHeading}
          </h2>
          <div className="mt-10 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {copy.features.map(({ icon: Icon, title, body }) => (
              <div key={title} className="rounded-2xl border border-zinc-200 bg-white p-6">
                <Icon className="h-6 w-6 text-blue-600" />
                <h3 className="mt-4 text-base font-semibold text-zinc-900">{title}</h3>
                <p className="mt-2 text-sm leading-relaxed text-zinc-600">{body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* How it works */}
      <section className="py-16 sm:py-20">
        <div className="mx-auto w-full max-w-4xl px-4 sm:px-6">
          <h2 className="text-center text-3xl font-bold tracking-tight text-zinc-900">
            {copy.howHeading}
          </h2>
          <ol className="mt-10 grid gap-8 sm:grid-cols-3">
            {copy.steps.map((step, index) => (
              <li key={step.lead} className="flex flex-col items-center gap-3 text-center">
                <span className="flex h-10 w-10 items-center justify-center rounded-full bg-blue-600 text-base font-bold text-white">
                  {index + 1}
                </span>
                <p className="text-[15px] leading-relaxed text-zinc-600">
                  <strong className="font-semibold text-zinc-900">{step.lead}</strong> {step.rest}
                </p>
              </li>
            ))}
          </ol>
        </div>
      </section>

      {/* Private by design */}
      <section className="mx-auto w-full max-w-6xl px-4 pb-16 sm:px-6 sm:pb-20">
        <div className="rounded-3xl bg-gradient-to-b from-blue-600 to-blue-900 px-6 py-12 text-white sm:px-12">
          <ShieldCheck className="h-8 w-8" />
          <h2 className="mt-4 text-2xl font-bold tracking-tight sm:text-3xl">
            {copy.privacy.heading}
          </h2>
          <p className="mt-4 max-w-3xl text-[15px] leading-relaxed text-white/[0.87]">
            {copy.privacy.p1}
          </p>
          <p className="mt-3 max-w-3xl text-[15px] leading-relaxed text-white/[0.87]">
            {copy.privacy.p2}
          </p>
          <p className="mt-6 text-[15px]">
            {copy.privacy.moreLead}{' '}
            <Link
              to="/privacy"
              className="font-semibold underline underline-offset-4 transition-colors hover:text-white/80"
            >
              {copy.privacy.moreLabel}
            </Link>
            .
          </p>
        </div>
      </section>

      {/* Final CTA */}
      <section className="border-t border-zinc-100 py-16 text-center sm:py-20">
        <div className="mx-auto w-full max-w-2xl px-4 sm:px-6">
          <h2 className="text-3xl font-bold tracking-tight text-zinc-900">{copy.finalCta.title}</h2>
          <p className="mt-3 text-zinc-600">{copy.finalCta.subtitle}</p>
          <Link to="/login" className={`mt-8 ${CTA_CLASSES}`}>
            {copy.finalCta.button}
          </Link>
        </div>
      </section>
    </main>
  );
}

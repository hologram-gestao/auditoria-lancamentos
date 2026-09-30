/**
 * Tour do produto (86e3gqfkf): "Veja o produto por dentro", entre "Como funciona" e
 * "Segurança". Cinco telas REAIS em abas, cada uma com uma frase e dois bullets.
 *
 * Os prints são cópias otimizadas de `Docs/manual/fonte/img/` (dado fictício de
 * "Cliente Exemplo Ltda"; figura com nome de dado de teste não entra), todos no tema
 * Hologram: anomalias e lançamento, que no manual estão no tema escuro, foram
 * recapturados no Hologram pelos cenários do gate de a11y que os geraram (86e3h0xcr,
 * mesmo dado mockado e mesmo recorte). Largura e
 * altura abaixo são as REAIS do arquivo em `public/landing/tour/` (um teste confere
 * o cabeçalho do PNG), então o `<Image>` reserva o espaço certo e não há salto.
 *
 * Este componente é server: monta os itens e entrega às abas, que são o único
 * pedaço cliente do tour (`landing-tour-tabs.tsx`).
 */
import { tour } from './content';
import { LandingSection } from './landing-section';
import { LandingTourTabs, type TourItemView } from './landing-tour-tabs';

type TourId = (typeof tour.items)[number]['id'];

export const TOUR_IMAGES: Record<TourId, { src: string; width: number; height: number }> = {
  conciliacao: { src: '/landing/tour/conciliacao.png', width: 1600, height: 929 },
  anomalias: { src: '/landing/tour/anomalias.png', width: 1600, height: 625 },
  lancamento: { src: '/landing/tour/lancamento.png', width: 1600, height: 710 },
  'de-para': { src: '/landing/tour/de-para.png', width: 1600, height: 929 },
  carteira: { src: '/landing/tour/carteira.png', width: 1600, height: 674 },
};

export function LandingTour() {
  const items: TourItemView[] = tour.items.map((item) => ({
    id: item.id,
    tab: item.tab,
    frameTitle: item.frameTitle,
    alt: item.alt,
    text: item.text,
    bullets: [...item.bullets],
    image: TOUR_IMAGES[item.id],
  }));
  return (
    <LandingSection id="por-dentro" eyebrow={tour.eyebrow} title={tour.title}>
      <div data-reveal>
        <LandingTourTabs
          items={items}
          labels={{
            tabs: tour.tabsLabel,
            pauseLabel: tour.pauseLabel,
            resumeLabel: tour.resumeLabel,
          }}
        />
      </div>
    </LandingSection>
  );
}

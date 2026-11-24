// Sonereli brand mark (the app icon), used where the wordmark or a control
// label already names the product.

export function SonereliMark({ size = 24 }: { size?: number }) {
  return <img src={`${import.meta.env.BASE_URL}favicon.svg`} width={size} height={size} alt="" />;
}

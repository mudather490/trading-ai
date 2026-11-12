import { notFound } from "next/navigation";
import { connection } from "next/server";
import { AppNav } from "@/components/AppNav";
import { MarketView } from "@/components/MarketView";
import { loadInstrument, requireUser } from "@/lib/data";

export const instant = false;

export default async function MarketDetailPage({
  params,
}: PageProps<"/markets/[symbol]">) {
  await connection();
  await requireUser();

  const { symbol } = await params;
  const instrument = await loadInstrument(decodeURIComponent(symbol));

  if (!instrument) notFound();

  return (
    <>
      <AppNav />
      <MarketView
        instrument={{
          id: instrument.id,
          symbol: instrument.symbol,
          display_name: instrument.display_name,
          venue: instrument.venue,
          asset_class: instrument.asset_class,
          data_quality: instrument.data_quality,
        }}
      />
    </>
  );
}

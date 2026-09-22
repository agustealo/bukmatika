import { ReaderClient } from "../../../../components/reader-client";

type ReaderPageProps = {
  params: Promise<{
    libraryEntryId: string;
    documentId: string;
  }>;
};

export default async function ReaderPage({ params }: ReaderPageProps) {
  const { libraryEntryId, documentId } = await params;
  return <ReaderClient libraryEntryId={libraryEntryId} documentId={documentId} />;
}

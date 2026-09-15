import { useInstanceName } from '../lib/workspace-context';
import { PullRequestReview } from '../components/files/PullRequestReview';

export function PullRequestsTab() {
  const instance = useInstanceName() ?? '';
  return <PullRequestReview key={instance} instance={instance} />;
}

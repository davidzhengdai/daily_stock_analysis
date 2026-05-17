import type React from 'react';
import { Star } from 'lucide-react';
import { cn } from '../../utils/cn';

interface FavoriteStockButtonProps {
  isWatched: boolean;
  disabled?: boolean;
  onToggle: () => void;
  className?: string;
}

export const FavoriteStockButton: React.FC<FavoriteStockButtonProps> = ({
  isWatched,
  disabled = false,
  onToggle,
  className = '',
}) => (
  <button
    type="button"
    title={isWatched ? '已加入自选股' : '添加到自选股'}
    aria-label={isWatched ? '已加入自选股' : '添加到自选股'}
    disabled={disabled}
    onClick={(event) => {
      event.stopPropagation();
      onToggle();
    }}
    className={cn(
      'inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-border/50 bg-card/70 text-secondary-text transition-colors hover:border-yellow-400/40 hover:bg-yellow-400/10 hover:text-yellow-400 disabled:pointer-events-none disabled:opacity-50',
      isWatched ? 'border-yellow-400/40 bg-yellow-400/10 text-yellow-400' : '',
      className,
    )}
  >
    <Star className="h-4 w-4" fill={isWatched ? 'currentColor' : 'none'} />
  </button>
);

export default FavoriteStockButton;

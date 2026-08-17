import React, { createContext, useContext, useState, ReactNode } from 'react';

type StrategyType = 'SUPERTREND' | 'MONTHLY_RSI';

interface StrategyContextType {
  activeStrategy: StrategyType;
  setActiveStrategy: (strategy: StrategyType) => void;
}

const StrategyContext = createContext<StrategyContextType | undefined>(undefined);

export const StrategyProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [activeStrategy, setActiveStrategy] = useState<StrategyType>('SUPERTREND');

  return (
    <StrategyContext.Provider value={{ activeStrategy, setActiveStrategy }}>
      {children}
    </StrategyContext.Provider>
  );
};

export const useStrategy = () => {
  const context = useContext(StrategyContext);
  if (context === undefined) {
    throw new Error('useStrategy must be used within a StrategyProvider');
  }
  return context;
};

import React, { createContext, useContext, useState, useEffect } from 'react';
import { api } from '../services/api';

type CompanyImageContextType = Record<string, string>;

const CompanyImageContext = createContext<CompanyImageContextType>({});

export const CompanyImageProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [images, setImages] = useState<CompanyImageContextType>({});

  useEffect(() => {
    // We add a custom API call here directly or through api service.
    // For simplicity, we just fetch it here.
    fetch('http://localhost:8000/api/companies/images')
      .then(res => res.json())
      .then(data => {
        setImages(data || {});
      })
      .catch(err => {
        console.error("Failed to fetch company images:", err);
      });
  }, []);

  return (
    <CompanyImageContext.Provider value={images}>
      {children}
    </CompanyImageContext.Provider>
  );
};

export const useCompanyImages = () => useContext(CompanyImageContext);

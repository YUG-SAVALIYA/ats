import React, { createContext, useContext, useState, useEffect } from 'react';
import { api } from '../services/api';

import { API_BASE } from '../services/api';

type CompanyImageContextType = Record<string, string>;

const CompanyImageContext = createContext<CompanyImageContextType>({});

export const CompanyImageProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [images, setImages] = useState<CompanyImageContextType>({});

  useEffect(() => {
    api.getCompanyImages()
      .then(data => {
        setImages(data || {});
      })
      .catch(err => {
        console.error("Failed to fetch company images via api client, trying public fallback:", err);
        const token = localStorage.getItem('ats_admin_token');
        fetch(`${API_BASE}/companies/images`, {
          headers: {
            'Content-Type': 'application/json',
            ...(token ? { 'Authorization': `Bearer ${token}` } : {})
          }
        })
          .then(res => res.json())
          .then(data => setImages(data || {}))
          .catch(e => console.error("Company images fallback also failed:", e));
      });
  }, []);

  return (
    <CompanyImageContext.Provider value={images}>
      {children}
    </CompanyImageContext.Provider>
  );
};

export const useCompanyImages = () => useContext(CompanyImageContext);
